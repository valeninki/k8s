# Actions optimization audit

**Scope:** read-only snapshot of live Forgejo bare repositories at `HEAD`, runner/Kubernetes resources, and checked-in manifests; 2026-09-27 17:42 UTC. No workflows were run, registries queried for image sizes, secrets read, settings changed, or builds measured. Paths and line numbers below refer to each repository's `HEAD` at inspection time. The older `forgejo-status-report.md` describes a previous zero-runner state; the **live** state now has one runner.

## Executive decision

**Do not add build capacity to the current DinD pod on `w1`.** Its `/var/lib/docker` is an unlimited root-filesystem `emptyDir` (~1.1 GiB used already), while only ~10.6 GiB is free on the node. One privileged TLS DinD runner has capacity one, but this does not cap disk or the child Docker containers independently. First cap disk and stop untrusted/cluster-changing jobs sharing that runner; then move ordinary Rust/Go checks to a dedicated, unprivileged Kubernetes-job/pod executor **only after verifying Forgejo runner compatibility and job isolation**. Keep one isolated Docker-capable path only for workflows that truly need Docker. Do not treat a `runs-on` label change alone as a Kubernetes executor.

## Workflow inventory

18 bare repositories were found across `valeninki`, `savew`, `parud`, `safely`, and `unix`. Seven have nine workflow files at valid `HEAD`; eight valid repositories have no workflow; `valeninki/nullroute`, `valeninki/tectonic`, `savew/test` have no valid `HEAD`, so their content cannot be ruled out on other refs. Inventory is not a historical/other-branch scan.

| Repository | Workflow | Triggers | `runs-on` | Main workload |
|---|---|---|---|---|
| `valeninki/steam-tui` | `.forgejo/workflows/ci.yaml` | push, pull_request | `ubuntu-latest` | Nix shell; Cargo format/check/3 clippy passes/tests; Go vet/race; Nix flake checks |
| `valeninki/runner-sanity-20260927173127` | `.forgejo/workflows/sanity.yml` | push | `ubuntu-latest` | scratch Node/Docker diagnostics |
| `savew/honeypot2` | `.github/workflows/cluster-benchmark.yml` | weekly schedule, workflow_call, workflow_dispatch | `ubuntu-latest` | Tailscale/kube-bench Kubernetes job, issue reporting |
| `savew/honeypot2` | `.github/workflows/k0s-deploy.yml` | push on `vars.yaml`, workflow_call, workflow_dispatch | `ubuntu-latest` | download tools; apply cluster configuration |
| `savew/honeypot2` | `.github/workflows/yaml-formatter.yml` | push, pull_request, workflow_dispatch, workflow_call | `ubuntu-latest` | install yamlfmt; format and commit YAML |
| `savew/spc` | `.github/workflows/build-release.yml` | `v*` tag, workflow_dispatch | `ubuntu-latest` + `ubuntu-24.04-arm` matrix; release on `ubuntu-latest` | CMake amd64/arm64 binaries; release |
| `savew/pardus-pen` | `.github/workflows/blank.yml` | push to master, workflow_dispatch | `ubuntu-latest` | Docker action with `debian:stable` package build, release |
| `parud/pardus-accessibility-tool` | `.github/workflows/build-deb.yml` | `v*` tag, workflow_dispatch | `ubuntu-latest` | apt dependencies, Debian package and release |
| `parud/pardus-share` | `.github/workflows/release.yml` | `v*` tag | `ubuntu-latest` | Flutter, Go, Linux packaging, GoReleaser |

No workflow at valid `HEAD`: `valeninki/{nixpkgs,netui,dotfiles.nix,k8s}`, `savew/dots.nix`, `parud/pardus-book`, `safely/jolen`, `unix/holepu`. The `ubuntu-24.04-arm` label is **not** among the live runner labels; the ARM job cannot complete on the inspected runner. A label is not proof of ARM hardware/emulation.

## Observed runner and storage envelope

| Item | Observed | Consequence |
|---|---|---|
| Forgejo runner | one Ready pod on `w1`, capacity 1; labels `ubuntu-latest:docker://node:20-bookworm`, `docker:docker://docker:dind`, `k8s-dind:docker://node:20-bookworm`; timeout 3h | ordinary `ubuntu-latest` job starts a Node Bookworm Docker job container, **not** a native Kubernetes job |
| Docker daemon | privileged `docker:28.3.0-dind` sidecar, TLS; `/var/lib/docker` node-backed unlimited `emptyDir`, ~1.1 GiB used | image/layer/build/workspace churn consumes node root disk; cache disappears on pod restart |
| Resource settings | runner 200m/256Mi request, 1 CPU/1Gi limit; DinD 250m/512Mi request, 1.5 CPU/2Gi limit; no ephemeral request/limit, namespace quota, or LimitRange | combined requests 450m/768Mi; limits 2.5 CPU/3Gi; Docker job CPU/memory are **not proven** bounded by those pod settings |
| `w1` | 4 CPU, ~7.55Gi RAM; sampled 851m CPU/3018Mi; 75.3Gi filesystem total, 63.9Gi used, ~10.6Gi free (86% used); DiskPressure=False | transient multi-GiB build can threaten disk before DiskPressure signals; node is not guaranteed to have 2 spare CPUs under load |
| Persistence | runner registration PVC 1Gi Longhorn, ~28Ki used; volume degraded, 2 of 3 desired replicas running; app repo PVC 10Gi and DB PVC 8Gi separate | runner PVC **does not** hold `/var/lib/docker`; artifact data/retention on Forgejo app were not measured |
| Cache/retention | runner `cache.enabled: false`; no explicit artifact policy in examined runner ConfigMap or local Forgejo app manifest | `actions/cache`/Cargo cache should not be assumed functional; actual Forgejo defaults and artifact location need checking |

**Pull-volume accounting:** known configured OCI image references are the runner `data.forgejo.org/forgejo/runner:6.4.0`, sidecar `docker:28.3.0-dind`, repeated default job image `node:20-bookworm`, `docker:dind` on a `docker`-label job, and `debian:stable` in `pardus-pen`. Nix packages, Rust toolchains, Flutter, Tailscale, Go and Actions code are extra network/storage downloads, not Docker-image bytes. **Total download size cannot be calculated honestly from YAML:** digests/platforms, existing layer cache, compression and registry manifests were not measured. Capture `docker system df -v`, registry manifest layer sizes by digest, and per-run before/after byte counts to set a baseline without pulling extra images for this audit.

**Disk planning estimates, not measurements:** budget roughly 0.5–1.5 GiB per distinct general-purpose job/base image when uncached, ~1–5+ GiB for a cold six-crate Rust debug `target/` plus Cargo registries, and several more GiB for a cold Nix store/flake build depending on substituters; `pardus-share` Flutter SDK may add several GiB. Layer decompression, writable layers and duplicate image versions can multiply compressed pull bytes. With ~10.6 GiB currently free and ~1.1 GiB Docker use already observed, a cold `steam-tui` Nix job is a plausible disk-exhaustion risk; these ranges are **not** observed per-run usage. Set initial per-job ephemeral budget 3–4 GiB and verify with actual peaks; Nix full checks may exceed it and should move to a separate node/remote builder or be excluded until measured. On a two-vCPU CX33 worker, two unconstrained rustc/Go race processes could occupy both cores for minutes; the observed `w1` exposes 4 CPU, so use the smaller 2-vCPU budget as a conservative placement target, not a description of `w1` capacity.

## Waste and risk, by exact workflow lines

- **`steam-tui/.forgejo/workflows/ci.yaml:3-9`:** push and PR run the *same* heavy job; pushing a PR branch can queue duplicate checks. A single runner processes them sequentially, extending latency; there is no cross-target matrix in this workflow.
- **`:10-13`:** checkout and `cachix/install-nix-action@v31` on the Node Bookworm Docker job. This is **Nix installation/package downloads inside DinD**, not `rust:latest` or `dtolnay/rust-toolchain`. The `github_access_token` input is a credential reference; its value was not inspected.
- **`:14-21`:** six distinct `nix develop --command` invocations: format, check, default/headless/all-feature clippy, all-feature tests. They can share package downloads during one job, but repeat dependency analysis and compile different feature/test profiles. No explicit Cargo cache or retained `target/`; ephemeral job workspace and runner cache disabled mean the next job is cold unless an unverified external Nix/Cargo cache helps.
- **`:22-28`:** Go race tests and `nix flake check` on *every* push/PR. `flake.nix` defines six checks (`steam-maintain`, `formatting`, `clippy`, `test-local`, `test-cm`, `test-all`) and an `acceptanceCheck` that repeats Cargo fmt/check/three clippy configurations, feature-specific tests, Go vet/tests/race. Although the Nix file intentionally shares a target directory within the acceptance derivation, the workflow runs both direct checks and the Nix check set. Nix derivation/substitute store use can dominate disk. No separate `cargo build --release` is currently in the workflow; do **not** claim repeated release passes. `Cargo.toml` release profile uses fat LTO, one codegen unit and `strip = "symbols"`; avoid a PR release build because that profile is CPU-heavy. `flake.nix` is x86_64-only; no Rust target cross-compilation exists in this workflow.
- **`spc/.github/workflows/build-release.yml:13-23,25-47,49-64`:** native amd64+ARM CMake matrix and a third release job require two runner architectures and artifact round trips. Correct for releases, not PRs; block/skip ARM only by policy if no real ARM capacity, rather than advertise fake ARM labels. Limit artifact retention to 1–3 days after confirming upload action/Forgejo compatibility.
- **`pardus-pen/.github/workflows/blank.yml:14-30`:** Docker action pulls `debian:stable` and builds a Debian package with workspace/output mounts; another image and package layers can accumulate in DinD. Keep on isolated Docker lane or use a prebuilt minimal Debian build image.
- **`pardus-accessibility-tool/.github/workflows/build-deb.yml:15-39` and `pardus-share/.github/workflows/release.yml:12-35`:** dependency installation/Flutter+Go packaging on release paths; do not trigger on PR, cache pinning by lock/toolchain, set max run time and short artifact retention.
- **`honeypot2/.github/workflows/cluster-benchmark.yml:20-48`, `k0s-deploy.yml:21-59`:** Tailscale cluster access plus Kubernetes job changes / `k0sctl apply`; **isolate** from untrusted PR runner and ordinary build cache. `yaml-formatter.yml:9-30` requests write permission and commits on a PR-capable workflow; audit fork/event token policy before enabling.
- **`runner-sanity-20260927173127/.forgejo/workflows/sanity.yml:6-11`:** diagnostic calls use `|| true`; success does not establish working Docker/Node. Retire scratch runs after validation.

## Low-footprint `steam-tui` workflow template

Replace `.forgejo/workflows/ci.yaml` **only after review**. The following uses the runner's existing `ubuntu-latest` label, so it can start on today's DinD deployment; it does **not** eliminate DinD. Its pinned minimum Rust matches workspace `rust-version = "1.88"`. It assumes apt network access and Debian `node:20-bookworm` job user can run `sudo` or is root; use a prebuilt image with the four native development packages if it cannot. The template intentionally omits Nix, release builds, artifacts and Actions cache; it checks every Rust library with `--lib` (all six workspace crates have `src/lib.rs`) but does not run binary/integration/feature-combination tests on every PR. Run the full suite separately on a scheduled/manual trusted lane. `CARGO_BUILD_JOBS=2` is a conservative maximum for a 2-vCPU node. Cargo test compilation may still need several GiB until caching is configured.

```yaml
name: fast-ci
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: steam-tui-fast-${{ github.ref }}
  cancel-in-progress: true
jobs:
  rust-fast:
    runs-on: ubuntu-latest
    timeout-minutes: 35
    env:
      CARGO_BUILD_JOBS: '2'
      CARGO_INCREMENTAL: '0'
      CARGO_TERM_COLOR: always
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Native dependencies
        run: |
          apt-get update
          DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev libdbus-1-dev libsecret-1-dev
          rm -rf /var/lib/apt/lists/*
      - uses: dtolnay/rust-toolchain@stable
        with:
          toolchain: '1.88.0'
          components: clippy, rustfmt
      - name: Fast Rust checks
        run: |
          cargo fmt --all -- --check
          cargo clippy --locked --workspace --all-targets -- -D warnings
          cargo test --locked --workspace --lib
```

If the Node job image lacks `apt-get` privileges or native libraries, use a **versioned, small prebuilt CI image** containing Node (for checkout), git, Rust 1.88+ with clippy/rustfmt and native dependencies; configure a dedicated `rust-small` label to that image and change `runs-on` accordingly. Pin the base image by digest after validation and reuse it on one node. Avoid using a fresh `rust:latest`, Nix installer and Node base in the same job. A compatible native Kubernetes pod executor is a *separate* runner/backend implementation to verify; the current Forgejo runner labels are Docker labels.

**Trusted full-validation lane (separate workflow, scheduled or manual, not every PR):** retain one `nix flake check` for full feature variants/Go race/packaging, avoid repeating its tests via preceding `nix develop` commands, use a 90-minute timeout and run only after a substituter or remote builder plus disk monitoring is ready. Go-only quick checks can instead run `go vet ./... && go test ./...` in a pinned small Go environment on PR; reserve `go test -race ./...` for full validation. Release-only tag job can build once with `cargo build --locked --release -p steam-tui` (profile already strips symbols), then upload **only** the binary with 1–3 day artifact retention when the Forgejo-compatible upload action is confirmed; avoid Nix + Cargo duplicate release builds. A `cargo-chef` planner/cook layer is useful if building an OCI image frequently, but on this existing Nix workflow it adds tooling and may not beat a measured binary/substituter cache; measure first.

## Cache, build and artifact policy

1. **Immediate disk safety:** provision an ephemeral-storage request/limit for runner/DinD and a namespace LimitRange/ResourceQuota as appropriate; separate Docker storage onto a bounded dedicated volume or node and monitor actual filesystem usage. Kubernetes `emptyDir.sizeLimit` alone may not hard-stop node usage on every filesystem/configuration: test eviction behavior. Set an alert at <=15 GiB free (already breached) and stop onboarding large jobs until headroom improves. Do not blindly `docker system prune` while builds run or remove Longhorn data. Make runner job CPU/memory/disk controls explicit in the actual executor, not just sidecar limits. Keep capacity 1; set `CARGO_BUILD_JOBS=2`, Go `-p 1` where needed, timeouts and cancellation. Watch co-located databases/control-plane load.
2. **Cache carefully:** runner cache is disabled, so do not copy an `actions/cache` snippet assuming it persists. First verify Forgejo cache service and action compatibility; prefer a bounded, per-toolchain+Cargo.lock cache of Cargo registry/git metadata, not arbitrary debug `target/`. For repeat Rust compilation, test `sccache` with a dedicated Garage S3 bucket using short-lived restricted credentials, bucket size/lifecycle quotas and cache TTL; use `RUSTC_WRAPPER=sccache` and a stable key namespace including Rust version/target/profile. Do not put secret values in logs or upload object keys that contain tokens. If a persistent local cache is used, quota and evict it explicitly. Cargo-chef is optional for OCI release image layers; not a mandatory extra PR stage.
3. **Image builds:** no current `steam-tui` workflow builds a Docker image. For future Dockerfiles, favor Buildah rootless where tested (or Kaniko only with a maintained/security-reviewed implementation; upstream Kaniko maintenance status must be checked), with remote registry layer cache, immutable base digest, single architecture by default and a bounded cache retention. Rootless Buildah/Kaniko still requires executor-specific security/storage testing and is not a drop-in replacement for workflows invoking arbitrary Docker actions.
4. **Retention:** verify the effective Forgejo Actions artifact/log retention and actual app PVC usage; no policy was visible in examined manifests. Propose global 7-day logs/artifacts for routine CI and 1–3 day intermediate binaries, with 30-day release artifact retention only if needed (published releases may follow a separate repository policy). Set upload-action `retention-days` where supported and periodically inventory DB records/object store usage. The expanded **8Gi DB PVC is not the same as artifact data volume**; measure DB and app PVC separately before attributing growth. Give build caches their own lifecycle and avoid storing bulky outputs in the DB.

## Rollout and verification

- **P0:** record `df` on `w1`, Docker `system df -v`, job peak root-FS and `emptyDir` use, image pull bytes and node CPU/memory before/after one approved non-production run; check whether `ubuntu-latest` child containers obey intended caps. Verify runner Longhorn degraded replica and available disk. Do not run destructive workflows as an audit test.
- **P1:** validate the fast template on a trusted branch after owner approval, compare elapsed time, network ingress and peak disk with baseline; confirm Forgejo `concurrency`, action versions, checkout credentials, native deps and toolchain compatibility. Keep the old workflow until the fast lane passes; then move full Nix validation to schedule/manual or protected tags.
- **P1:** establish isolated privileged Docker runner for `pardus-pen` only if needed, separate protected deployment runner for `honeypot2`, and real ARM capacity for `spc` (or remove ARM release target by product decision). Do not give PRs cluster credentials.
- **P2:** pilot an unprivileged K8s job/pod runner with strict service account/RBAC, per-job CPU ~1–1.5 cores, memory 2–3Gi, ephemeral 3–4Gi, capacity 1, timeout 35 minutes, network policy and no Docker socket; measure cold and warm Cargo runs and enable bounded sccache/Garage only after observing improvement. Confirm executor support and action semantics before retiring DinD. Recheck node root-FS >=20% free and no DB/control-plane impact under one compile.

**Verdict:** retain the present DinD runner only as a short-term, isolated compatibility lane with **enforced** disk/CPU controls and cleanup/monitoring. Prefer a tested unprivileged Kubernetes pod/job executor for routine `steam-tui` checks. Neither the runner ConfigMap nor workflow YAML currently supplies such an executor; a deployment/design change is required and is outside this read-only audit.


## 2026-09-27 native-executor implementation update (supersedes the earlier fast-CI template)

**RBAC applied; native executor blocked.** `forgejo/runner-k8s-rbac.yml` is tracked in `forgejo/kustomization.yml` and was applied to the live `forgejo-k8s` namespace. It creates `ServiceAccount/forgejo-runner-k8s`, `Role/forgejo-runner-k8s-role`, and `RoleBinding/forgejo-runner-k8s-rb`. The Role grants only the requested namespaced pod, pod exec/log, secret and configmap verbs; no ClusterRole or binding to the default account. Note that namespaced `get/list secrets` can read existing application secrets in this namespace. The SA is **not assigned to any runner** while the native backend is unsupported.

Validation transcript (live cluster):

```text
$ kubectl apply --dry-run=server -f forgejo/runner-k8s-rbac.yml
serviceaccount/forgejo-runner-k8s created (server dry run)
role.rbac.authorization.k8s.io/forgejo-runner-k8s-role created (server dry run)
rolebinding.rbac.authorization.k8s.io/forgejo-runner-k8s-rb created (server dry run)
$ kubectl apply -f forgejo/runner-k8s-rbac.yml
serviceaccount/forgejo-runner-k8s created
role.rbac.authorization.k8s.io/forgejo-runner-k8s-role created
rolebinding.rbac.authorization.k8s.io/forgejo-runner-k8s-rb created
$ kubectl auth can-i create pods --as=system:serviceaccount:forgejo-k8s:forgejo-runner-k8s -n forgejo-k8s
yes
$ kubectl auth can-i create deployments --as=system:serviceaccount:forgejo-k8s:forgejo-runner-k8s -n forgejo-k8s
no
$ kubectl auth can-i create pods --as=system:serviceaccount:forgejo-k8s:forgejo-runner-k8s -n default
no
```

**Compatibility finding:** the live `forgejo-runner` Deployment uses `data.forgejo.org/forgejo/runner:6.4.0`, with DinD sidecar. Version-tagged upstream source [`config.go`](https://code.forgejo.org/forgejo/runner/src/tag/v6.4.0/internal/pkg/config/config.go) defines `Config` fields `log`, `runner`, `cache`, `container`, `host`, but **no `kubernetes` field**; its example config documents `host` and `docker://` labels only. The requested `kubernetes.namespace`, `pull_policy`, `service_account_name`, and `resources` entries would not activate a Kubernetes executor. `k8s-native:host` means in-process execution **inside the runner pod**, not per-job Kubernetes pods. Changing this label or granting the runner a token would give a false positive and potentially expose secrets. No second runner was deployed or registered, no `action-...` pod was produced, and **there is no native execution or cleanup log proof**. The existing DinD Deployment remains ready and unchanged. Do not remove it. A verified Kubernetes-native runner backend compatible with Forgejo Actions is a prerequisite; then pilot with dedicated registration state, separate ServiceAccount, capacity 1, ephemeral-storage limits, and an explicit pod-watch + no-DinD + cleanup test.

The old `fast-ci` example above **omits Nix and Go checks** and must not replace `steam-tui` CI as written. The corrected multi-language replacement appears below; it is a proposal, not an executed workflow.

### Proposed complete `steam-tui/.forgejo/workflows/ci.yaml` (not yet pushed)

The following is staged in `/tmp/steam-tui-workflow-audit/.forgejo/workflows/ci.yaml` at source revision `0f08f632be6ca6d6ab84ec3692f339aac2987223`, but **not deployed to the Forgejo repository**. It preserves the Rust, Go, and Nix checks in `flake.nix`; the single acceptance derivation shares Cargo's `target` within a run and avoids eight duplicate `nix develop` invocations. It uses the *existing* `ubuntu-latest` Docker runner, so this is not native Kubernetes. `Cargo.toml` declares `rust-version = "1.88"`; the locked Nix inputs select actual toolchain versions. This proposal does **not** pin an independent `rust:1.88-slim` or `golang:1.24-alpine` image: doing so before confirming action runtime, native libraries, and the exact duplicated check matrix would risk dropping checks. The `nix flake check` includes Rust fmt, all-target/all-feature check, three Clippy variants, local/headless-CM/all-feature tests, Go vet/tests/race, and the Go `steam-maintain` package check. The Nix `buildRustPackage` derivation also builds during its check, so a no-release-build claim would be false.

```yaml
name: CI

on:
  push:
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  checks:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    env:
      CARGO_BUILD_JOBS: "2"
      CARGO_INCREMENTAL: "0"
      NIX_CONFIG: "experimental-features = nix-command flakes\nmax-jobs = 1\ncores = 2"
    steps:
      - uses: actions/checkout@v4
      - uses: cachix/install-nix-action@v31
        with:
          github_access_token: ${{ secrets.GITHUB_TOKEN }}
      # flake.nix's acceptanceCheck runs Rust fmt/check/clippy/tests and Go
      # vet/tests/race tests in one derivation with a shared Cargo target.
      # Repeating those checks in nix develop compiles the same crates twice.
      - name: Rust, Go, and flake checks
        run: nix flake check
```

**Verification of the proposal:** YAML parsed using PyYAML `BaseLoader`; `git diff --check` passed; `nix flake check --no-build` returned `all checks passed!` and showed the five acceptance check aliases resolve to one derivation. This evaluates expressions only. No full compile/test, resource-profile measurement, workflow run, or native job pod occurred. `CARGO_BUILD_JOBS=2`, `CARGO_INCREMENTAL=0`, `NIX_CONFIG` (`max-jobs=1`, `cores=2`), capacity-one runner, and a 90-minute timeout bound intended parallel CPU churn, but **do not guarantee** disk use; Docker `emptyDir` is capped at 4Gi and must be watched. Forgejo support for `concurrency.cancel-in-progress` is not live-tested. Resource requests/limits for ephemeral pods and image-cache reuse remain unverified because this runner has no native pod backend. Avoid promising a measured low-resource profile.

**Next gate:** choose and validate an actual Forgejo-compatible Kubernetes pod executor, then register a separate label and watch a test job pod appear, log successful execution without DinD, and disappear after completion. Only after that can Rust/Go/Nix image variants be benchmarked with resource telemetry and the original Docker lane reconsidered.

## 2026-09-27 DinD scratch-branch validation (final: failed safety gate)

The Kubernetes-native runner path is closed for runner 6.4.0; the existing one-capacity DinD runner is the official engine. The earlier native-executor recommendation and 90-minute proposed workflow above are historical and **superseded** by this section and `forgejo/steam-tui-ci.proposed.yaml` (25-minute timeout).

- Scratch branch `ci-optimization-test`, commit `5899e2d87f85e27943fb7eaf620eb9f789263def`, was pushed **only** to that branch. Forgejo Actions run [#3](https://git.valentinus.dev/valeninki/steam-tui/actions/runs/3) started on 2026-09-27 at 18:26:39 UTC. Do not merge before its result is known.
- Workflow: `concurrency.group: ${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true`, job `timeout-minutes: 25`. Limits in the workflow are `CARGO_BUILD_JOBS=1`, `GOMAXPROCS=1`, `GOFLAGS=-p=1`, Nix `max-jobs=1`, `cores=1`; these reduce parallel work but are **not** an enforceable 1.5-core job quota. DinD alone has a 1.5 CPU Kubernetes limit; the runner container has a separate 1 CPU limit, and actual nested Docker child cgroups are not proven independently capped.
- `ubuntu-latest` resolves to the existing `node:20-bookworm` Docker job image; this image does **not** ship Nix. `cachix/install-nix-action@v31` installs Nix during the job. `NIX_CONFIG` enables `nix-command flakes`; `nix flake check` uses pinned flake inputs instead of `nix-channel` updates, but a cold run downloads Nix and flake dependencies. It is incorrect to claim this uses a preinstalled `nixos/nix:latest` image or avoids the Nix installer download.
- The flake has two unique check derivations: a Go `steam-maintain` build and a Rust/Go acceptance derivation aliased as formatting, clippy, test-local, test-cm, test-all. Acceptance builds the all-feature Rust binary and performs cargo fmt, workspace all-target/all-feature check, three Clippy modes with `-D warnings`, local/headless/all-feature Rust tests, Go vet, Go tests and Go race tests. `nix flake check --no-build` previously only evaluated; the live run is the execution proof pending below. It does not build every flake package variant.
- Measurement: `/var/lib/docker` is a 4Gi `emptyDir` on the `w1` root filesystem. `df` on this path reports **node filesystem** use, not emptyDir quota use; the sampler records `du -sk /var/lib/docker` and `kubectl top pod --containers` alongside `ssh w1/w2 df -Pk /`. Pre-run Docker usage was 184 KiB; node root free was about 14% (`w1`) and 47% (`w2`). Sampler at `/tmp/steam-tui-ci-monitor.csv` runs about every 7 seconds. If either root filesystem goes below 10% free, or Docker usage reaches 3,500,000 KiB (~3.34 GiB), it deletes the runner pod to abort the job. Metrics-server samples are not precise instant peaks; child-container memory and CPU may not be fully reflected by pod metrics. Prune runs every 30 minutes, so it does not protect a 25-minute cold build from a 4Gi excursion.

**Run #3 result (superseded attempt):** Failure after **1m3s**. Setup pulled `node:20-bookworm`, then failed to resolve `cachix/install-nix-action@v31` at `https://data.forgejo.org/cachix/install-nix-action` (`repository not found`). Checkout, Nix install, Rust, Go and flake steps were canceled and **did not pass**. Sampled peak Docker storage 2,281,348 KiB (2.176 GiB), DinD CPU 1,284m, DinD RAM 116Mi, runner CPU 704m, runner RAM 167Mi; these container peak samples happened at different times, so do not add maxima as a contemporaneous pod peak. Min sampled worker free: w1 14%, w2 47%. No abort threshold was reached. This is not a successful benchmark.

**Run #4 result:** corrected installer to `https://github.com/cachix/install-nix-action@v31`, commit `9d55679`. Forgejo [run #4](https://git.valentinus.dev/valeninki/steam-tui/actions/runs/4) failed after about 14 seconds: checkout passed; the Nix action failed because the `node:20-bookworm` job image has no `sudo` (`install-nix.sh: line 91: sudo: command not found`). Rust/Go/flake step skipped. Docker usage measured 1,144,660 KiB at start; the only later `du` sample raced Docker overlay deletion and returned an error, so the run #4 peak is **unknown**, not zero. Min sampled worker free: w1 14%, w2 47%. No abort.

**Run #5 result: safety-aborted, not passed.** Commit `cc8ccc2` adds `sudo` and `xz-utils`; [run #5](https://git.valentinus.dev/valeninki/steam-tui/actions/runs/5) passed checkout and Nix installation (installer fetched `nix-2.35.2` as a ~25.8MiB tarball) and began `nix flake check`. Before the acceptance derivation even built, while checking package outputs and fetching flake input paths, Docker usage reached **3,637,720 KiB = 3.470 GiB**, ~86.7% of 4Gi. This is the highest **sampled** `du` value, not a true instantaneous peak. The safety monitor deleted the runner pod at 18:31:05 UTC, below the 4Gi ceiling; the worker roots never fell below 10% free (sample minimum w1 13%, w2 47%). The runner replacement scheduled onto `w2`. As last checked after replacement pod was Ready, Forgejo still showed run #5 as `Running`; this is a stale/in-flight status after a killed runner, not proof that checks continue. An authenticated Forgejo administrator should cancel/resolve this run and preserve its logs before deleting the scratch branch. From first observed job timestamp ~18:30:05 through forced abort ~18:31:05 was about 60 seconds; this is **not** a completed-run duration nor evidence of the <8-minute goal. Sampled DinD peak CPU **232m**, RAM **127Mi**; sampled runner peak CPU **151m**, RAM **17Mi**. These pod-container metrics do not prove nested job-container peak RAM/CPU, and a sampled low CPU during download cannot certify a 1.5 CPU hard boundary. The Rust 1.88+, Go and Nix acceptance assertions **did not finish and are not verified as passing**. Nix flakes were enabled and execution started, but `nix flake check` did not complete. No separate rust/go checks were silently omitted from the YAML, but none can be counted green. The old runner's 30-minute prune interval offered no protection against this rapid cold-store growth.

**Benchmark summary (sampled; not a successful full-suite result):**

| Attempt | Duration / outcome | Peak Docker `/var/lib/docker` | Peak DinD CPU / RAM | Peak runner CPU / RAM | Rust / Go / Nix checks |
|---|---|---|---|---|---|
| #3 | 1m3s; action resolution failure | 2,281,348 KiB (2.176 GiB) | 1,284m / 116Mi | 704m / 167Mi | not started |
| #4 | ~14s; missing `sudo` | unknown (one valid 1,144,660 KiB sample) | 77m / 90Mi | 44m / 230Mi | not started |
| #5 | ~60s until forced safety abort; final Forgejo status pending | **3,637,720 KiB (3.470 GiB)** | 232m / 127Mi | 151m / 17Mi | flake started; assertions not completed |

The table gives independent per-container sampled maxima, not synchronized total pod peaks or child-container maxima. No total successful duration, successful peak RAM/CPU, or under-eight-minute result exists.

**Final merge recommendation: DO NOT MERGE** `forgejo/steam-tui-ci.proposed.yaml` into `main` under the current 4Gi DinD budget. This is a real capacity blocker, not a successful benchmark. The requested strict 1.5 CPU job boundary is also not proven: DinD has a 1.5 CPU limit and runner has a separate 1 CPU limit; the workflow concurrency and `cores=1` reduce work, not enforce a combined pod/job quota. The proposal now has the exact requested 25-minute timeout/concurrency and a working installer path, but a cold `nix flake check` uses nearly all bounded storage *before tests*. Do not raise the emptyDir limit on these workers without a separate disk-capacity review. Use a prebuilt, digest-pinned Node+Nix job image and a measured remote Nix binary cache/remote builder on a separate bounded volume, or reduce the derivation footprint without dropping any Rust/Go/flake assertions; then rerun the complete suite with <4Gi peak and a proven per-job CPU cgroup <=1.5 and report actual duration, RAM and CPU. Note: a prebuilt Nix image alone may not eliminate downloaded Nix store inputs. `nixos/nix:latest` is **not** the current image and does not necessarily supply Node for `actions/checkout`.

**Branch cleanup (only after reviewing run #5 and preserving logs):** `git -C /tmp/steam-tui-ci-optimization-run push ssh://git@git.valentinus.dev/valeninki/steam-tui.git --delete ci-optimization-test`; then `git -C /tmp/steam-tui-workflow-audit worktree remove /tmp/steam-tui-ci-optimization-run` and `git -C /tmp/steam-tui-workflow-audit branch -D ci-optimization-test`. Do not delete `main`, force push, or merge the failing branch. The scratch branch was left in place for investigation.
