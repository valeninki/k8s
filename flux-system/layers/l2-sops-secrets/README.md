# SOPS Secret Layer

This layer is intentionally empty until an off-cluster SOPS age or KMS root of
trust is provisioned. It will contain encrypted Secrets for the infrastructure
and application credentials referenced by the later layers.

The Flux Git SSH identity is not placed here because source-controller needs it
before Flux can fetch this repository. Bootstrap automation must provision that
Secret first.

## Secrets currently created outside Git

Until then, these Secrets must exist before the layers that use them. Where a
bootstrap kustomization exists, copy its `.env.example` file(s) to the name
without `.example`, fill them in and run `kubectl apply -k <dir>/` from the
repository root.

| Namespace | Secret | Keys | Bootstrap |
|-----------|--------|------|-----------|
| `flux-system` | `forgejo-flux-runtime` | `identity`, `identity.pub`, `known_hosts` | manual |
| `kube-system` | `cloudflare-api-token-secret` | `api-token` | `cert-manager/` |
| `longhorn-system` | `cluster-key` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINTS` | `longhorn/` |
| `forgejo-k8s` | `db-secret` | `username`, `password` | `forgejo/` |
| `forgejo-k8s` | `forgejo-admin-secret` | `username`, `password` | `forgejo/` |
| `forgejo-k8s` | `forgejo-runner-secret` | `token` | manual (runner registration token) |
| `forgejo-k8s` | `flake-bot-deploy-key` | `id_ed25519` | manual |
| `forgejo-k8s` | `shark-renovate-token` | `token` | manual (Forgejo token) |
| `forgejo-k8s` | `shark-github-token` | `token` | manual (GitHub token) |
| `nextcloud-k8s` | `nextcloud-app-secret` | `nextcloud_admin_username`, `nextcloud_admin_password` | `nextcloud/` |
| `nextcloud-k8s` | `nextcloud-db-secret` | `nextcloud_db_username`, `nextcloud_db_password`, `nextcloud_db_admin_password` | `nextcloud/` |
| `metrics-k8s` | `vm-grafana` | `admin-user`, `admin-pass`, `ldap-toml` | `metrics/` |
| `commafeed-k8s` | `commafeed-postgresql` | `database`, `username`, `password` | `commafeed/` |
| `vaultwarden-k8s` | `vaultwarden-postgresql` | `database`, `username`, `password` | `vaultwarden/` |

The `wildcard-valentinus.dev-tls` Secret is issued by cert-manager in
`traefik` and copied to the application namespaces by reflector.
