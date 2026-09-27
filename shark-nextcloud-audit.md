# Shark / Nextcloud update audit

Audited read-only on 2026-09-27 UTC. No job was started, no Git ref was changed, and no credential value was read or printed.

## Execution backend and schedule

- The relevant bot is Kubernetes CronJob `forgejo-k8s/shark-renovate`, not a Forgejo Action or host timer. It runs `renovate/renovate:41.140.1` against `valeninki/k8s` on Forgejo (`flux-system/layers/l5-applications/shark-renovate-cronjob.yaml:1-9,45-70`). Schedule: `0 6 * * 6` in `Europe/Istanbul` (Saturday 06:00 local / 03:00 UTC while Istanbul is UTC+3); `concurrencyPolicy: Forbid`, not suspended. The last schedule was 2026-09-26 03:00:00Z; Job `shark-renovate-29839860` completed successfully at 03:00:22Z (1/1 succeeded).
- A different CronJob, `forgejo-k8s/shark-nixpkgs-updater`, runs Sunday 03:00 UTC and clones **`valeninki/nixpkgs`**, not `k8s`; its flake/package update loop cannot update the Nextcloud HelmRelease (`flux-system/layers/l5-applications/shark-nixpkgs-updater-cronjob.yaml:1-8,59-85`). It also completed on 2026-09-27. No tracked `.forgejo/workflows/` or `.github/workflows/` file in this repository and no matching local systemd timer was found. Flux reconciles `./flux-system` from branch `k0s` (`flux-system/gotk-sync.yaml`).

## Root cause: Nextcloud was never extracted

The successful Renovate run's info log reports **`managers: {"flux":{"fileCount":1,"depCount":1}}`**, with total also exactly one file and one dependency. The public Forgejo Dependency Dashboard (issue #1, last updated 2026-09-12) likewise lists only `flux-system/gotk-components.yaml` / `fluxcd/flux2`, not Nextcloud. The run reports no Nextcloud extraction or registry/API error. In the exact running Renovate release (41.140.1), `lib/modules/manager/flux/index.ts` sets the default `managerFilePatterns` from `systemManifestFileNameRegex`; `lib/modules/manager/flux/common.ts` defines that regex as `(?:^|/)gotk-components\.ya?ml$`. Thus the one scanned file is `flux-system/gotk-components.yaml`, and **`flux-system/layers/l5-applications/nextcloud.yaml` does not match**. Enabling the `flux` manager in `renovate.json:7-14` does not expand its default file pattern. There is no custom manager/file-pattern override. The update never reached chart lookup, branch creation, or PR creation. This is the observed failure point, rather than a missed run, `flake.lock`-only bot, Git divergence, or demonstrated authentication/rate-limit failure.

The active Nextcloud `HelmRelease` has `spec.chart.spec.chart: nextcloud`, **`version: 9.2.6`** (exact semver), and `sourceRef: {kind: HelmRepository, name: nextcloud}` (`flux-system/layers/l5-applications/nextcloud.yaml:15-22`). The matching `HelmRepository` is declared with namespace `flux-system` and URL `https://nextcloud.github.io/helm/` (`flux-system/sources.yaml:90-98`). The HelmRelease itself has `metadata.namespace: flux-system`, satisfying Renovate's HelmRelease/source matching requirements once scanned. There are **no bot annotations** or Flux image policy/update markers on this pin. The active manifest also has `busybox:latest` in an init container (`nextcloud.yaml:34-37`); it is not the chart version. `nextcloud/application.yml` has a legacy Argo chart range `9.*`, but is outside the active `./flux-system` Flux path and does not change this exact pin.

A read-only fetch of the upstream chart `index.yaml` on 2026-09-27 shows `9.2.7` and `9.3.0` newer than `9.2.6` (both published 2026-09-19). This checks reachability from the audit host, **not** registry connectivity from the Renovate pod. The job's info-level log contains no HTTP 429, TLS, DNS, or chart request failure; because the manifest was not extracted, it does not establish that the pod queried this Helm repository.

## Git state and credentials

- Local `k0s` is clean and even with `origin/k0s` (`git rev-list --left-right --count`: `0 0`). A read-only `git ls-remote --heads origin` returned only `refs/heads/k0s` at the same commit. No live remote Shark/Nextcloud/Renovate branch blocks this update. A local stale remote-tracking Renovate ref is **not** a live remote branch; do not prune it as part of this audit.
- The CronJob references `shark-renovate-token` and `shark-github-token`; both Secret objects exist, and the finished job successfully accessed the Forgejo repository. Secret existence and repository read access do **not** prove current push or PR-create permission. A public read-only Forgejo pull-list query showed only two closed historical Flux update PRs (numbers 2 and 3), no Nextcloud PR. Since Nextcloud was never extracted, no push/PR authorization check was exercised for it. The separate nixpkgs job uses `flake-bot-deploy-key` and its own PR path; that key is not Renovate's Git credential for `k8s`.
- The cluster has no `imagepolicies.image.toolkit.fluxcd.io` or `imageupdateautomations.image.toolkit.fluxcd.io` CRDs in the read-only CRD listing. No Flux image automation is governing this Helm chart version.

## Remediation

1. In `renovate.json`, extend **`flux.managerFilePatterns`** with the regex `/^flux-system\/.*\.ya?ml$/` (matches `.yaml` and `.yml` throughout that tree), while keeping the default `gotk-components.yaml` match. Confirm syntax against Renovate 41.140.1's config schema before committing. The `flux` manager already supports HelmRelease/HelmRepository chart extraction; a generic regex/custom manager is not needed for this standard resource.
2. Through the normal reviewed GitOps change, verify that the *next scheduled* run's extraction count increases beyond one and that it recognizes `nextcloud` chart `9.2.6` from the declared repository. Do not force a run for this audit. Review the proposed chart changes and app compatibility before merging, especially the `9.3.0` minor release; an update to `9.2.7` is also available.
3. If extraction succeeds but no PR appears, inspect the next run's sanitized debug-level dependency/branch decisions and the Forgejo dependency dashboard/PR limits (`prConcurrentLimit: 3`); then separately verify the Renovate bot's repo write and PR-create permissions without displaying tokens. Investigate DNS/TLS/429 only if the next run actually shows a chart lookup error. Avoid conflating these potential later blockers with this proven file-pattern failure.

Evidence: live `kubectl` CronJob/Job metadata and `kubectl logs job/shark-renovate-29839860 -c renovate`; repository manifests and `renovate.json`; read-only `git` refs and Forgejo pull listing; upstream Helm index and Renovate 41.140.1 source (`https://raw.githubusercontent.com/renovatebot/renovate/41.140.1/lib/modules/manager/flux/{index,common}.ts`).

## Remediation and verification — 2026-09-27 UTC

The earlier "Remediation" section above is the historical read-only finding. The following records the applied change and test. The repository-root `renovate.json` (not a ConfigMap) was pushed to `k0s` as commit `03a2098` after a successful read-only/dry-run check. The matching regex covers both `.yaml` and `.yml` in all `flux-system/` subdirectories; this also retains `gotk-components.yaml`. The broad minor/patch automerge rule was removed, top-level `automerge: false` was added, and Kubernetes/Helm managers plus Nextcloud have explicit `automerge: false`. No workloads were restarted or drained.

### Exact configuration patch

```diff
diff --git a/renovate.json b/renovate.json
index 676e4ee..aa4f3ea 100644
--- a/renovate.json
+++ b/renovate.json
@@ -12,13 +12,22 @@
     "kubernetes",
     "nix"
   ],
+  "automerge": false,
+  "flux": {
+    "managerFilePatterns": ["/^flux-system\\/.*\\.ya?ml$/"]
+  },
   "packageRules": [
     {
-      "description": "Automerge minor and patch dependency updates",
-      "matchUpdateTypes": ["minor", "patch"],
-      "automerge": true,
-      "automergeType": "pr",
-      "platformAutomerge": false
+      "description": "Never automerge Kubernetes and Helm dependencies",
+      "matchManagers": ["flux", "kubernetes", "helm-values"],
+      "automerge": false
+    },
+    {
+      "description": "Hold Nextcloud for storage and application audit",
+      "matchPackageNames": ["nextcloud", "nextcloud/*"],
+      "automerge": false,
+      "prCreation": "not-pending",
+      "labels": ["dependencies", "nextcloud", "hold-storage-audit"]
     }
   ]
 }
```

### Extraction evidence (dry-run first)

A one-off Job `forgejo-k8s/shark-discovery-test` was generated from CronJob `shark-renovate` with `RENOVATE_DRY_RUN=full`, `RENOVATE_LOG_LEVEL=debug`, and `RENOVATE_FORCE` set to the proposed `flux`, `automerge`, and `packageRules` values. This was necessary because the live remote initially had the old configuration. The Job succeeded (1/1), and `dryRun: full` prevented branch, PR, and issue writes. Selected log lines (secrets excluded):

```text
DEBUG: Using file pattern: /^flux-system\/.*\.ya?ml$/ for manager flux
DEBUG: Matched 67 file(s) for manager flux: ... flux-system/layers/l5-applications/nextcloud.yaml, ... flux-system/sources.yaml
 INFO: Dependency extraction complete (repository=valeninki/k8s, baseBranch=k0s)
       "managers": {"flux": {"fileCount": 16, "depCount": 17}},
       "total": {"fileCount": 16, "depCount": 17}
             "packageFile": "flux-system/layers/l5-applications/nextcloud.yaml",
             "deps": [
               {
                 "depName": "nextcloud",
                 "currentValue": "9.2.6",
                 "datasource": "helm",
                 "registryUrls": ["https://nextcloud.github.io/helm/"],
                 "updates": [
                   {
                     "bucket": "non-major",
                     "newVersion": "9.3.0",
                     "newValue": "9.3.0",
                     "newDigest": "42ed80ff2d8dd86490f19e99aeafc953f8a339a033d6e695ed94e02b8c378127",
                     "releaseTimestamp": "2026-09-19T15:49:20.479Z",
                     "newVersionAgeInDays": 8,
                     "newMajor": 9,
                     "newMinor": 3,
                     "newPatch": 0,
                     "updateType": "minor",
                     "isBreaking": false,
                     "libYears": 0.08992853358066971,
                     "branchName": "renovate/nextcloud-9.x"
 INFO: DRY-RUN: Would commit files to branch renovate/nextcloud-9.x
 INFO: DRY-RUN: Would ensure Dependency Dashboard
 INFO: Repository finished (repository=valeninki/k8s)
```

`Matched 67` counts candidate YAML files, while `fileCount: 16` counts files from which Flux extracted dependencies. The 17 dependencies include chart versions, a `busybox:latest` image (unversioned/skipped), the Flux sync reference (unversioned), and the Forgejo OCI source digest. Chart lookup succeeded for Nextcloud. There was no YAML parse crash. The run emitted a separate warning for a missing Forgejo OCI `latest` digest; it did not block Nextcloud discovery.

### Forgejo Dependency Dashboard (Issue #1)

The normal one-off Job `forgejo-k8s/shark-discovery-verify` ran after commit `03a2098` was confirmed on Forgejo. It succeeded (1/1; Renovate reported `result: done`), with the same `flux: {fileCount: 16, depCount: 17}` extraction count. The info log recorded `PR created` for cert-manager (#4) and cloudnative-pg (#5); it did not report a Nextcloud PR. The dashboard was updated at **2026-09-27T23:13:41Z** and is open at [Issue #1](https://git.valentinus.dev/valeninki/k8s/issues/1). It explicitly shows:

```text
## Rate-Limited
 - [ ] <!-- unlimit-branch=renovate/nextcloud-9.x -->chore(deps): update helm release nextcloud to v9.3.0
## Detected dependencies
<details><summary>flux-system/layers/l5-applications/nextcloud.yaml</summary>
 - `nextcloud 9.2.6`
```

The default hourly PR limit created two PRs first. Nextcloud is **pending/rate-limited**, not an open PR; no dashboard checkbox was clicked and no limit was overridden. Public Forgejo pull listing showed PRs #4 and #5 open and `merged: false`; no automatic merge into `k0s` was observed. The missing Forgejo OCI digest warning is unrelated to Nextcloud and remains to investigate separately. Because the config disables automerge, review chart and storage compatibility before merging any proposed Nextcloud update. No cluster workload was restarted or drained.
