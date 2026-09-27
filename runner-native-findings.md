# Forgejo runner native Kubernetes executor: compatibility finding

## Decision

**Do not switch this runner to a `kubernetes` executor.** The installed `data.forgejo.org/forgejo/runner:6.4.0` (`forgejo-runner version v6.4.0`, checked with `kubectl exec`) has no native Kubernetes job executor and no Kubernetes configuration fields. Adding `container.kubernetes`, `runner.executor: kubernetes`, a `kubernetes://` label, or pod-creation RBAC will not turn its jobs into pods. In particular, the YAML loader uses `yaml.Unmarshal` into a fixed Go struct; an unknown YAML key may be silently ignored rather than proving support.

## Verified evidence

- Live `forgejo-k8s/forgejo-runner` Deployment uses one runner container at `runner:6.4.0`, a privileged `docker:28.3.0-dind` sidecar, Docker TLS over localhost port 2376, `automountServiceAccountToken: false`, a PVC for `/data/.runner`, and an `emptyDir` for Docker storage (`sizeLimit: 4Gi` currently). Runner command registers three `docker://` labels if its PVC has no `.runner` file, then runs `daemon --config /config/config.yaml`. The Deployment is outside the checked-in Forgejo Kustomize app.
- Live `forgejo-runner-config` has `runner.capacity: 1`, `timeout: 3h`, three `docker://` labels, `cache.enabled: false`, and `container.privileged: false`, `docker_host: "-"`, `valid_volumes: []`. There is no Kubernetes executor setting in use.
- The **v6.4.0 tagged source** [`internal/pkg/config/config.go`](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/internal/pkg/config/config.go) defines only top-level `log`, `runner`, `cache`, `container`, `host`. `container` has Docker network, Docker host, privileged, options, workdir, volume allowance, pull/rebuild flags. See also the release's [`config.example.yaml`](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/internal/pkg/config/config.example.yaml).
- The same release's [`internal/pkg/labels/labels.go`](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/internal/pkg/labels/labels.go) accepts only `host`, `docker`, `lxc` schemes; a `kubernetes` scheme returns `unsupported schema`. [`internal/app/run/runner.go`](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/internal/app/run/runner.go) constructs `nektos/act`'s `runner.Config` with Docker-specific `ContainerDaemonSocket`, `ContainerNetworkMode`, and `Privileged`, then starts its plan executor. No Kubernetes API client or pod executor is wired in. The release's [Kubernetes example](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/examples/kubernetes/README.md) explicitly deploys **privileged Docker-in-Docker in Kubernetes**, not Kubernetes-native jobs.

## Minimal safe configuration now

Keep the existing Docker executor and `docker://` label mapping. Retain `runner.capacity: 1`, `cache.enabled: false`, `container.privileged: false`, `container.valid_volumes: []`, and the existing runner pod's `automountServiceAccountToken: false`. Do not grant the runner pod Kubernetes pod/secret creation privileges. Keep the existing scoped 4Gi Docker-storage `emptyDir` and its cleanup only as disk-pressure mitigation, **not** a per-job disk or CPU/memory quota. Preserve the current working setup until a concrete replacement executor, exact image digest, configuration schema, and isolation policy have been tested. Existing runner and DinD resource limits bound the pod containers, not individual jobs within DinD.

A minimal compatible config fragment (existing live settings; **not** a migration) is:

```yaml
runner:
  file: /data/.runner
  capacity: 1
  timeout: 3h
  labels:
    - ubuntu-latest:docker://node:20-bookworm
    - docker:docker://docker:dind
    - k8s-dind:docker://node:20-bookworm
cache:
  enabled: false
container:
  privileged: false
  docker_host: "-"
  valid_volumes: []
```

## Proof plan before any future native migration

1. Pin a specific candidate runner *implementation* and digest. Read the corresponding tagged config struct, label parser, and job execution source or official documentation. Confirm that it really starts isolated Kubernetes job pods and state exact supported config keys; do not infer support from a Kubernetes deployment example or a successful YAML parse.
2. In a separate namespace, with separate runner registration/token/labels, test a candidate config and a minimal ServiceAccount/Role restricted to that namespace. Never share the old runner's PVC `.runner` registration, and keep its Docker labels unchanged to permit rollback. Do not grant cluster-wide permissions or secret read unless source and tests prove a strict need.
3. Prove a `runs-on` job creates a new pod (`kubectl get pods -w` and audit logs), succeeds at checkout, shell steps, artifact and cache (if enabled), and removes pods after success, failure, timeout, and cancellation. Test two simultaneous runs against the advertised capacity and isolate job secrets, workspace, network, security context, CPU/memory and ephemeral-storage requests/limits. A per-job storage guarantee needs a separately verified storage/quota mechanism.
4. Test Docker-dependent jobs separately (build/push and service containers): Kubernetes-native jobs do not imply a Docker daemon. Explicitly define build strategy, registry access, and supported workflow semantics. Then route one dedicated label to the new runner, measure failures and cleanup, and only then migrate other labels. Rollback is label routing to the unchanged DinD runner.

Inspection commands (read-only): `kubectl -n forgejo-k8s get deployment forgejo-runner -o yaml`, `kubectl -n forgejo-k8s get configmap forgejo-runner-config -o yaml`, `kubectl -n forgejo-k8s exec deployment/forgejo-runner -c runner -- forgejo-runner --version`.
