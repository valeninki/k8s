# Remediation report — 2026-09-27 UTC

**Status: PHASES 1–3 PARTIAL; PHASE 4 GATE FAILED.** Earlier sections preserve the 12:33–12:37 UTC baseline; the current execution is recorded in the update below. No replica-count change, drain, or reboot was made. The requested confirmations of zero failed services, zero active warning events, and 12 two-replica volumes **cannot be given**. Context: `cloud-context`. Baseline is `remediation-plan.md` (read in full); later measurements below are point-in-time unless stated otherwise.

## Before / after and verification matrix

| Scope / check | Before (runbook baseline) | After (this audit) | Result |
|---|---|---|---|
| m1 node | Ready; DNSConfigForming; SSH key login blocked; boot state historical | Ready=True, DiskPressure=False, MemoryPressure=False; DNS warning still recurring; SSH key login denied; `systemctl --failed`, boot free space, update timer and resolver source not independently checked | **Open** |
| w1 node | Ready; Longhorn disk not schedulable; DNS warning; SSH key login blocked | Ready=True, DiskPressure=False, MemoryPressure=False (Kubernetes condition only); Longhorn disk Ready=True, **Schedulable=False**, 11,848,908,800 B free versus 12,126,938,726 B minimum; SSH key login denied; DNS warning still recurring; systemd/boot/timer unchecked | **Open**; Kubernetes DiskPressure=False does not override Longhorn disk pressure |
| w2 node | Ready; DNS warning; SSH available but sudo needs authentication | Ready=True, DiskPressure=False, MemoryPressure=False; `systemctl --failed` lists **two failed** units: `wg-quick@wg0.service`, `wg-quick@awg0.service`; `/boot` 227 MiB available; `dnf5-automatic.timer` active, last service Result=success / ExecMainStatus=0; DNS warning still recurring | **Fail** zero-services requirement; do not disable tunnels until owner confirms their role |
| Pods / claims | Three nodes Ready; 12 Longhorn volumes attached | 3/3 Ready, 0 node DiskPressure, 0 node MemoryPressure; 12/12 PVCs Bound; no pending/failed pods observed; completed Jobs excluded | **Pass at snapshot**, not 24 h |
| Warning events | 21 DNSConfigForming objects; one FreeDiskSpaceFailed | 22 Warning event objects: **21 DNSConfigForming + one FreeDiskSpaceFailed**; DNS event counts increased by **54** between ~12:33:49 and 12:36:51 UTC, with last observed warning timestamp 12:35:45 UTC. Retained historical disk event alone does not prove new disk failure. | **Fail**: active new DNS warnings. No event objects were deleted. |
| Longhorn copies / disks | 12/12 healthy but 1 replica each; w1 unschedulable | 12/12 attached/healthy, **12/12 at numberOfReplicas=1**, 12 running replicas (w1=3, w2=9), **0/12 at requested 2**; w1 disk not schedulable, w2 Ready/Schedulable with 39,426,457,600 B free, less than provisioned 40 GiB Nextcloud volume | **Fail** 2-copy goal; no replica changes safe yet |
| Backups and CNPG | Backups stale (all latest July 28); CNPG single instance, no verified restore | Six volumes have fresh completed Backup CRs dated Sep 27; six latest still July 28. No independent remote/isolated restore proven. CNPG `forgejo-db` 1/1 and ContinuousArchiving=True but no CNPG Backup/ScheduledBackup objects or tested base-backup/PITR/logical restore | **Fail** recovery gate; backup CR != tested recovery |
| m1/w1 OS boot/update | Historical checks only | `uname -r`, `/boot` >100 MiB, timer active, last update service success, `systemctl --failed` **unverified** on both because SSH authentication failed | **Unknown**; do not claim zero failures |
| Network/ingress | Traefik one pod on m1; September 11 OOM | Deployment/pod 1/1 on m1; 45–46 Mi live use versus 128 Mi request / 512 Mi limit; 6 historical restarts, last OOMKilled Sep 11 23:58:01 UTC. VIP 172.16.20.50 HTTPS probes for git/cloud/vault from this workstation timed out at connection (not proof external ingress is down). w2 `resolvectl query example.com` succeeded; cluster-local query via w2 systemd-resolved timed out (not a valid pod CoreDNS test). | **Open** routing and historical peak validation |
| GitOps | 8/8 Flux Kustomizations Ready; dirty working tree | 8/8 Kustomizations, 14/14 HelmReleases, 1/1 GitRepository Ready; no current reconciliation failure shown. Live `longhorn` StorageClass requests 3 replicas, Helm default 2, existing volumes 1. Working tree still contains unrelated staged/unstaged changes. | **Pass Ready only**; policy drift remains |

## DNS diagnosis and safe change gate

On **w2**, `/etc/resolv.conf` points to generated `/run/systemd/resolve/resolv.conf` (uplink mode). It emits global `1.1.1.1`, `9.9.9.9`, `8.8.8.8` plus `185.12.64.1`, `185.12.64.2` from NetworkManager connection `cloud-init eth0`, i.e. **five IPv4 nameservers**. systemd-resolved also has Tailscale's split-DNS servers on `tailscale0`, but those do not appear in the five emitted IPv4 `nameserver` lines. Kubelet reports dropping excess servers and using the first three on m1, w1, and w2. This establishes the w2 source; the matching warning text on m1/w1 suggests the same issue, **but their resolver configuration was not inspected** because key-based SSH was denied.

**No DNS configuration change was made.** w2 requires interactive sudo; m1/w1 require authenticated access. Changing a cloud-init/NetworkManager profile remotely without verified fallback could remove management/API connectivity. The safe next change is to secure node login and out-of-band rollback first; capture each node's resolved/NM/cloud-init source, confirm chosen <=3 upstream servers answer both public and required internal domains, then adjust the persistent owner **one node at a time** (for example remove redundant DHCP-provided DNS or change resolved global DNS, depending on source). Preserve Tailscale split-DNS. Never edit the generated resolver file. Verify `/etc/resolv.conf`, public and internal DNS, API/SSH connectivity, and event **count/lastTimestamp deltas** after each node; restore the saved profile via console on regression. DNSConfigForming event objects can remain until expiry: zero **new** warnings, not an empty event listing, is the correct pass condition.

## Traefik ownership, memory, and routing risk

The live owner is Flux `HelmRelease flux-system/traefik` (chart 41.2.0, app v3.7.10), **not established as** legacy `traefik/application.yml`. Deployment uses `hostPort` 80/443 and a selector pinning its only eligible pod to m1. Service is LoadBalancer with MetalLB VIP `172.16.20.50` and `externalIPs: [167.233.190.40]`; seven `IngressRoute`s expose `dns`, `rss`, `git`, `dash`, `music`, `cloud`, and `vault` under `*.valentinus.dev`. These are topology observations; real external routing paths were **not** validated. Do not roll out or restart this singleton before validating public/internal ingress paths, service/hostPort reachability and alternate-node placement.

The September 11 OOMKilled reason is retained in current pod status (exit 137), so **OOM risk is not disproven** by 45–46 Mi current usage. VictoriaMetrics currently reports zero TSDB series; no trustworthy 7–30-day peak is available. **No memory adjustment is justified from the snapshot alone.** Establish retained memory telemetry and investigate OOM cause, then change only the authoritative Flux HelmRelease in a clean reviewed branch if peaks warrant it. First design at least two eligible replicas, distinct nodes, nonconflicting ports and tested LB/external traffic behavior; `maxSurge: 1` alone cannot ensure a safe rollout with a single hostPort-eligible node. The live release has `revisionHistoryLimit: 0`, leaving no prior ReplicaSet as a simple rollback.

## Outstanding blockers and architectural recommendations

1. **Storage and recovery:** obtain fresh completed backup for **all 12** volumes, independently verify remote target and restore isolated copies; establish and test CNPG base-backup/PITR or logical restore with database consistency. Add worker disk capacity for provisioned sizes, reservations, snapshots and rebuild overhead; clear w1 Longhorn scheduling before moving existing volumes, one at a time, to **two healthy replicas split across w1/w2**. A third independent storage node is needed for three node-separated replicas. Do not drain/reboot or reduce/delete the only good replica meanwhile. Map the Sep 24 w1 ext4 errors to a real device/PVC or document them as unresolved and monitor I/O.
2. **Singleton control plane / ingress / DB:** m1 is the only control-plane node and Traefik hostPort target; `forgejo-db` has one CNPG instance. Plan an odd-numbered **three-control-plane-node** quorum with redundant API front end, at least two Traefik pods on independent eligible nodes with tested public ingress failover, and CNPG multi-instance replication across failure domains plus tested promotion and backup restore. Check quorum, PDBs, workload placement and storage capacity before node failures or upgrades. Two storage workers alone do not provide a third storage failure domain.
3. **OS and DNS:** restore authenticated, safe node access; inspect m1/w1 failed services, boot space and update timers. Resolve w2 failed WireGuard units only after confirming whether tunnels are required. Fix the persistent resolver source separately on all nodes and track fresh events. Do not claim zero active warnings or zero failed services yet.
4. **GitOps:** isolate a clean authoritative Flux repository/branch. Align future Longhorn class/default replica policy without mutating immutable StorageClass parameters or reconciling the dirty checkout. Verify actual ownership before any Traefik resource edit.

**24-hour follow-up:** After approved changes, record a UTC start, capture initial warning UIDs/counts/lastTimestamp and systemd/Longhorn/Flux/pod states, repeat **every verification-matrix check** after each phase and after at least 24 hours (the runbook calls for 24–48 h), and compare fresh event timestamps/counts, disk headroom, memory peaks, restores and route probes. No 24-hour post-remediation verification was performed during this audit. Full zero-downtime resilience is **not achieved**.


## Execution update — September 27, after 12:47 UTC

Scope: Phases 1–3 only. `kubectl config current-context` was `cloud-context`; m1/w1/w2 were Ready. The unrelated staged and unstaged Git changes were left untouched. **No replica count was patched, no node was drained or rebooted, and nothing under `/var/lib/longhorn` or an active database directory was deleted.**

### Phase 1 — backups and Forgejo logical dump

Longhorn backup-volume verification is listed below. Dates are UTC; **all 12 have a completed September 27 backup at 100% progress**. The `kubectl get backups.longhorn.io -o custom-columns=VOLUME:.spec.volumeName,STATE:.status.state,CREATED:.status.createdAt` command renders `<none>` for VOLUME and CREATED on this installed CRD; the correct fields are `.status.volumeName` and `.status.backupCreatedAt`. `Completed` means the CR reports success, **not** that an isolated restore has been proven. All 12 latest completed Backup CRs report 100% progress and `s3://cluster-backup@garage/` URLs. No independent object-store listing or restore was done.

| Volume | BackupVolume latest | Latest completed backup UTC | Backup CR | Sep 27 completed? |
|---|---|---|---|---|
| `pvc-1b04fd1e-c0ec-436c-bd96-1264e6d17a5b` | `2026-09-27T12:51:18Z` | `2026-09-27T12:51:41Z` | `phase1-backup-20260927-1b04fd1e` | **PASS** |
| `pvc-1d55d063-4b64-4cbd-8e46-015c77cf729e` | `2026-09-27T12:52:09Z` | `2026-09-27T12:53:38Z` | `phase1-backup-20260927-1d55d063` | **PASS** |
| `pvc-213b0e93-54ce-4564-9db2-8d3527549fdc` | `2026-09-27T12:53:51Z` | `2026-09-27T12:54:15Z` | `phase1-backup-20260927-213b0e93` | **PASS** |
| `pvc-2db9d661-87cb-4e76-8799-3ae94fbd15c3` | `2026-09-27T12:28:30Z` | `2026-09-27T12:28:40Z` | `backup-d54a00c3904f412c` | **PASS** |
| `pvc-4d8bd367-c061-4a10-8c6b-5d2c9bef6542` | `2026-09-27T12:28:45Z` | `2026-09-27T12:29:07Z` | `backup-93b05ef17d69475f` | **PASS** |
| `pvc-67af9edd-bd28-434b-9adf-a95cb39b99fb` | `2026-09-27T12:54:38Z` | `2026-09-27T13:01:06Z` | `phase1-backup-20260927-67af9edd` | **PASS** |
| `pvc-69e423ed-46d7-4076-b37d-86b7dfa93a68` | `2026-09-27T12:26:28Z` | `2026-09-27T12:26:35Z` | `backup-95d6f80b85a846e5` | **PASS** |
| `pvc-799169cf-21e0-4ea0-b9e3-0ea8ceecc9e2` | `2026-09-27T12:29:15Z` | `2026-09-27T12:29:38Z` | `backup-ae5edbcd63a0434a` | **PASS** |
| `pvc-815cc13c-6623-4178-8b64-eaa6d4a30552` | `2026-09-27T12:29:47Z` | `2026-09-27T12:30:02Z` | `backup-4f777fcee211429c` | **PASS** |
| `pvc-ad0c149d-6eee-4ff7-9406-7daa57d25fbc` | `2026-09-27T12:28:19Z` | `2026-09-27T12:28:22Z` | `backup-75a8c060cbef4725` | **PASS** |
| `pvc-c366367c-a434-44f3-acd3-37fab38567dc` | `2026-09-27T13:01:23Z` | `2026-09-27T13:02:06Z` | `phase1-backup-20260927-c366367c` | **PASS** |
| `pvc-e91ecf63-a961-42c8-b8e4-24bc4e3103d2` | `2026-09-27T13:02:23Z` | `2026-09-27T13:02:42Z` | `phase1-backup-20260927-e91ecf63` | **PASS** |

`forgejo-db-1` was Running and primary when `pg_dumpall -U postgres` was piped through `gzip` to `./forgejo_db_backup_2026-09-27.sql.gz` with pipeline failure detection. `gzip -t` passed; compressed size **103,020 bytes** (101 KiB), uncompressed size **1,280,232 bytes**. The PostgreSQL cluster dump header and completion marker were present. SHA-256: `935f044b1511e3e306af5f783cab679c0fc35196499cb75bb75ebe23f8b3fb9d`. File permission is `0600`. **No test restore was performed**; `pg_dumpall` does not make a single cross-database transaction or prove CNPG PITR recovery.

### Phase 2 — w1 disk pressure

| Check | Before | After | Outcome |
|---|---|---|---|
| `df -h /` on w1 | 76G size, 65G used, **11G available, 87% used** | **11G available, 87% used** | No cleanup: clipboard-sourced sudo credential rejected. |
| Longhorn w1 disk | Ready=True, **Schedulable=False**, 11,848,908,800 B available vs 12,126,938,726 B minimum | Not cleared | Fails 15% scheduling threshold; also fails runbook's <80%-used headroom target. |

w1 journal usage was about 233.8M, so a 500M vacuum alone offered no useful relief. No image prune or journal vacuum ran. A valid sudo credential and a runtime/cache assessment are required before cleanup; adding capacity may still be needed.

### Phase 3 — boot, services, WireGuard and DNS

| Node | Running kernel | `/boot` free | `systemctl --failed` | Action |
|---|---|---|---|---|
| m1 | 7.2.4-100.fc43.x86_64 | **9.2 MiB** (99% used) | `dnf5-automatic.service` failed, Result=exit-code, ExecMainStatus=1 | No privileged changes; sudo credential rejected. |
| w1 | 7.2.4-100.fc43.x86_64 | **9.2 MiB** (99% used) | `dnf5-automatic.service` failed, Result=exit-code, ExecMainStatus=1 | No privileged changes; sudo credential rejected. |
| w2 | 7.2.7-100.fc43.x86_64 (Kubernetes node info) | 227 MiB (earlier audit) | `wg-quick@wg0.service`, `wg-quick@awg0.service` failed | Pending verified tunnel retirement and authorized sudo access. |

On m1 and w1 the unprivileged `dnf5 remove --assumeno` dry run proposed only the 7.1.10 kernel-core, kernel-modules, kernel-modules-core and dependent kernel metapackage; 7.1.13 and the running 7.2.4 would remain. No package removal occurred. The automatic update timers were reported enabled/active, but their services' most recent runs failed because the 7.2.7 kernel update needs more `/boot` space. Do not call this phase complete until >100 MiB is free and service results are successful.

w2 `/etc/wireguard` is root-only (`0700`); an unprivileged listing was denied. Its Sep 24 unit logs say both `/etc/wireguard/wg0.conf` and `/etc/wireguard/awg0.conf` did not exist at service start. Their current presence and retirement status are **not verified**, so the services were not disabled or masked.

On **all three nodes**, `/etc/resolv.conf` points to `/run/systemd/resolve/resolv.conf` and lists **five** upstream IPv4 nameservers: global `1.1.1.1`, `9.9.9.9`, `8.8.8.8` plus DHCP/NetworkManager `185.12.64.1` and `185.12.64.2` (order varies). The Tailscale split-DNS link is separate. No generated file or network profile was changed. The DNSConfigForming selector still returned **21 event objects**, cumulative count **304,515**, with observed last timestamps as late as 12:50 UTC. Existing event objects are retained; compare count and lastTimestamp to determine whether new warnings stop after a future change.

**Phase 4 readiness: NO.** All 12 backup CRs completed, but independent restore checks are absent, w1 is unschedulable and lacks rebuild headroom, boot/update failures persist, and DNS warnings are still generated. Do not increment `spec.numberOfReplicas` or proceed with Phase 4 until the recovery and capacity gates pass. This does not establish zero-downtime resilience.


## Clipboard-authenticated execution update — 2026-09-27 13:13 UTC

The earlier access failure in this report is superseded: SSH and `sudo -S -v` succeeded on **m1, w1 and w2** using a clipboard-fed SSH askpass helper and stdin for sudo. The helper was removed after use. No password was stored in this report or passed as a command argument. Context remained `cloud-context`; all three nodes were Ready. This section records only the latest execution, not a 24-hour observation window.

### Phase 1: isolated recovery check

- The completed September 27 backup `backup-75a8c060cbef4725` for `commafeed-k8s/commafeed-data-pvc` (`pvc-ad0c149d-6eee-4ff7-9406-7daa57d25fbc`, 256 MiB) was restored from its `s3://cluster-backup@garage/` URL into **new** Longhorn volume `longhorn-system/verify-restore-20260927` with `numberOfReplicas: 1`. No production volume was changed. Longhorn reported restore complete (`restoreRequired=False`, Restore condition False), then detached the volume. Static PV `verify-restore-pv-20260927`, PVC `backup-verify-scratch/verify-restore-pvc`, and a read-only pod on w2 mounted the filesystem. Pod completed with `RESTORE_READ_OK`; `du` reported 20 KiB and `find` counted **zero regular files**. This proves a backup URL could produce a mountable filesystem, **not application-file correctness or recovery of every volume**. The scratch namespace/PV/PVC/volume are intentionally retained for review because deleting the Longhorn volume would remove files under `/var/lib/longhorn`, which the guardrail prohibits. The completed reader pod is also retained. Remove these only after the operator clarifies and authorizes scratch-resource teardown.
- `forgejo_db_backup_2026-09-27.sql.gz`: `gzip -t` passed; decompression showed the PostgreSQL database cluster dump header and completion marker. This is **dump readability only**. No isolated PostgreSQL restore or CNPG base-backup/PITR verification was performed; the CNPG recovery gate remains open. No claim is made that the Longhorn backup of the running CNPG PVC is application-consistent.

### Phase 2: w1 headroom

| Check | Before | After | Result |
|---|---|---|---|
| w1 root `df -h /` | 76G total, 65G used, **11G available, 87% used** | 76G total, 64G used, **12G available, 85% used** | Below the runbook's <80%-used target; large replicas still need added independent capacity. |
| Longhorn w1 disk | Ready=True, **Schedulable=False**; 11,848,908,800 B available versus 12,126,938,726 B minimum | Ready=True, **Schedulable=True**; 13,002,342,400 B available at final check | Scheduling lock cleared at this snapshot; capacity still insufficient for broad rebuilds. |

`k0s ctr -n k8s.io images prune --all` succeeded but removed no images (32 before and after; no measurable space gain). Registry probe to Docker Hub answered HTTP 401, consistent with reachability. `dnf5 clean packages` removed 1 GiB of downloaded package cache outside protected data directories. No journal vacuum ran: total journals were only 233.8 MiB, below the 500 MiB cap, and historical disk-error evidence should be preserved. Nothing was manually deleted in `/var/lib/longhorn`, `/var/lib/k0s` or active DB directories.

### Phase 3: boot, updates, tunnels and DNS

| Node | Inactive kernel transaction | `/boot` after | Update status |
|---|---|---|---|
| m1 | Dry-run reviewed, then removed only exact 7.1.10-100.fc43 `kernel`, `kernel-core`, `kernel-modules`, `kernel-modules-core`; retained 7.1.13 and running 7.2.4 | **252 MiB free, 73% used** (was 9.2 MiB free) | Timer active and scheduled; last service still Result=exit-code / ExecMainStatus=1 from before cleanup. |
| w1 | Same reviewed transaction, retained 7.1.13 and running 7.2.4 | **252 MiB free, 73% used** (was 9.2 MiB free) | Timer active and scheduled; last service still failed from before cleanup. |

**Do not restart `dnf5-automatic.service` under the current no-reboot rule.** `/etc/dnf/automatic.conf` on both nodes has `apply_updates = true`, `download_updates = true`, and **`reboot = when-needed`**. Restart could install updates and reboot; even the next already-scheduled timer run may do so. The service and timer were not restarted or modified. Arrange an approved no-reboot update policy/change window before verifying a fresh successful service run. The timers remain active; this future automatic-reboot risk requires operator action.

w2 read-only audit: `/etc/wireguard` exists but is empty; `/etc/amnezia/amneziawg` exists and is root-only (contents not read). The `amneziawg` module (1.0.20251009) and WireGuard 1.0.0 loaded in the kernel on Sep 24. `wg-quick@wg0.service` and `wg-quick@awg0.service` are enabled but failed because `/etc/wireguard/wg0.conf` and `awg0.conf` were missing at start. No tunnel unit, config or module was changed.

All three nodes still advertise **five** IPv4 nameservers through generated `/etc/resolv.conf` (`1.1.1.1`, `9.9.9.9`, `8.8.8.8`, `185.12.64.1`, `185.12.64.2`; order of the last two varies). This exceeds kubelet's maximum of three and can continue `DNSConfigForming` warnings. No DNS source was changed because the owner and rollback path require a separate reviewed network change; do not edit the generated file.

**Sequential 1 → 2 replica expansion: NOT READY.** w1 passed the minimal scheduling threshold but is 85% full, the scratch restore checked only an empty application volume, CNPG has no tested isolated database restore, and the 40 GiB provisioned Nextcloud volume lacks safe peer headroom. All 12 production Longhorn volumes still specify one replica. Add worker capacity and independently verify representative nonempty restores and database recovery before considering any sequential expansion. No replica count was patched; no node was rebooted or drained.


## Recovery, tunnel, disk and DNS follow-up — 2026-09-27 13:25 UTC

**Outcome:** w2 AmneziaWG is operational; a populated Vaultwarden backup and a Forgejo SQL logical restore were tested in isolation. w1 is still 85% full and DNS still advertises five upstream servers. **Replica scaling remains on hold.** No production replica count, node drain or reboot was performed. The earlier empty Commafeed scratch restore remains in `backup-verify-scratch`; the new recovery scratch resources were removed.

### w2 AmneziaWG / WireGuard

- Running kernel `7.2.7-100.fc43.x86_64`: `amneziawg` and `wireguard` loaded; DKMS `amneziawg/1.0.0` installed for the running kernel (also 7.2.5/7.2.6).
- `/etc/amnezia/amneziawg/awg0.conf` exists, root-owned mode 0600, as does `awg0.conf.pre-master-20260720160736`; no tunnel secrets were read into the report. `/etc/wireguard` has no top-level tunnel config, and neither `/etc/wireguard/wg0.conf` nor `/etc/wireguard/awg0.conf` was found.
- `awg0` is **UP**, its route is present, and `awg-quick@awg0.service` is enabled/active with the correct `awg-quick` tool. Four peers are configured; three have handshaken at some point and one had a handshake in the preceding five minutes with nonzero traffic. This does not prove all peers are reachable.
- The enabled, failed `wg-quick@awg0.service` and `wg-quick@wg0.service` targeted the wrong tool or absent configs. Only these two units were **disabled**; the active `awg-quick@awg0` and interface remained active/UP after verification. Their historical failed state was not reset; no tunnel config, route or service restart was changed.

### Populated Longhorn recovery proof

- Completed backup `backup-95d6f80b85a846e5`, Vaultwarden PVC `vaultwarden-k8s/vaultwarden-data-pvc`, snapshot `2026-09-27T12:26:28Z`, was restored into separate Longhorn volume `recovery-vw-data-20260927` in disposable namespace `recovery-proof-20260927`. Restore completed; a read-only pod on w2 mounted it and exited 0 with `BACKUP_READ_OK`.
- Mounted `/restore` held **72 regular files, 816K**. Nonsecret evidence: `/restore/icon_cache/github.com.png` **33,270 B**, mtime `2026-09-09 19:09:59 UTC`; `/restore/icon_cache/stackoverflow.com.png` **7,406 B**, mtime `2026-04-26 14:14:09 UTC`. No private file contents were printed. This supersedes the earlier empty-filesystem-only proof, but does not verify every volume or application login.
- The new namespace, disposable PostgreSQL pod, static PV/PVC and Longhorn restore volume were confirmed absent after cleanup. The **pre-existing** `backup-verify-scratch` and its Commafeed restore were left untouched; coordinate separate authorized teardown of those older resources.

### Forgejo database logical restore

- `forgejo_db_backup_2026-09-27.sql.gz` (103,020 compressed bytes) passed `gzip -t`. It is a PostgreSQL 18.1 `pg_dumpall` cluster dump. An isolated `postgres:18.1-trixie` pod used an emptyDir and Unix socket only.
- Strict `psql -X -v ON_ERROR_STOP=1` on the **unedited** dump stopped at line 20: `CREATE ROLE postgres` conflicts with the bootstrap role created by initdb. A trial under an alternate bootstrap user stopped at a `GRANT ... GRANTED BY postgres` at line 35. A fresh retry with **only the duplicate `CREATE ROLE postgres;` statement removed**, retaining remaining SQL, exited 0.
- In the restored `forgejo` database, `public."user"` has **10 rows**, `public.repository` **17 rows**, and there are **128 public tables**. This is a valid logical schema/data restore with the documented bootstrap normalization, not proof of the untouched dump's direct replay, CNPG PITR/base-backup recoverability, or every attachment. Keep that distinction in recovery instructions.

### Ranked w1 disk consumers and headroom

`df -hT /`: btrfs `/dev/sda3`, **76G total / 63G used / 12G available (85%)**. `btrfs filesystem usage /` estimates 11.51 GiB free. `du -x` estimates below are not additive where parent and child paths overlap.

| Rank | Path | Measured use | Assessment |
|---|---|---:|---|
| 1 | `/var/lib/longhorn/replicas` | **51G** | Live replicas; do **not** delete manually. Largest replica `pvc-67af9edd-bd28-434b-9adf-a95cb39b99fb-62caa3ef` 46G; next 2.4G and 2.1G. |
| 2 | `/var/lib/k0s/containerd` | **12G** | 8.9G overlay snapshots, 2.8G content blobs; review runtime-aware GC, never delete snapshots directly. Prior image prune had no measurable gain. |
| 3 | `/usr` | **3.6G** | `/usr/local` 1.5G; assess packages before action. |
| 4 | `/var/log` | **628M** | Journal 233.8M; preserve September 24 disk-error evidence. |
| 5 | `/var/cache` | **207M** | Modest potential cleanup with owner approval. |
| 6 | `/home` / `/opt` | **197M / 188M** | `/home/valen/.cache` 196M; small gains only. |

`/var/lib/systemd/coredump` is 69M; `/var/tmp` and `/tmp` 0; `/var/crash` absent. `lsof +L1` could not run because `lsof` is not installed. A root `/proc/*/fd` scan found 11 deleted-file descriptors including three Longhorn `volume-head` images with large **apparent** sizes (40/10/2 GiB), a memory-backed 2TiB memfd and small journal/dbus descriptors; apparent/sparse sizes are not reclaimable disk usage. Do not kill Longhorn processes or infer a 52GiB cleanup from these entries.

Longhorn w1 disk is Ready/Schedulable at this snapshot but only **12.30 GiB available**, 52.00 GiB scheduled; w2 has 36.62 GiB available and 14.75 GiB scheduled with 5 GiB reserved. Neither the 85% w1 root nor the peer capacity safely supports the 40 GiB Nextcloud rebuild plus overhead. The plan's <80% root target and independent rebuild headroom are **not met**. Expansion or reviewed relocation/retention work is needed; no more cache cleanup was performed.

### DNS warning source and unresolved change

On w1, `/etc/resolv.conf` resolves to generated systemd-resolved output (`/run/systemd/resolve/resolv.conf`). `/etc/systemd/resolved.conf` sets global `DNS=1.1.1.1 9.9.9.9 8.8.8.8`; NetworkManager `cloud-init eth0` also accepts DHCP `185.12.64.2` and `185.12.64.1`. Thus **five upstream IPv4 servers** are advertised; Tailscale split DNS remains separate. Prior checks found the same five-server pattern on m1 and w2. Kubelet retains only the first three and emits `DNSConfigForming`.

A guarded candidate was to set only the global resolved `DNS=` to `1.1.1.1`, retaining two DHCP servers for a total of three, with automatic rollback and DNS/SSH checks on one node at a time. **Not applied:** clipboard-sourced sudo authentication failed before backup or mutation. No resolver profile, generated file, interface or Tailscale setting was modified. Before a future attempt, place a valid credential in the clipboard and verify a safe rollback path. Check all nodes separately; the upstream count **after this attempt remains five**, not three. At baseline there were 21 event objects, cumulative count 304,934 and latest timestamp 13:15:37 UTC; a later snapshot increased by 110 and had latest timestamp 13:23:11 UTC. Old event objects need not disappear, but these new warnings prove DNS has not stopped.

**Release gate:** isolated file and logical SQL restores improve recovery evidence, but the untouched dump needs documented normalization, CNPG base-backup/PITR remains untested, and w1 lacks storage headroom. Leave every production `spec.numberOfReplicas=1` until the storage and recovery gates in `remediation-plan.md` pass.


## Phase 4 incremental follow-up — 2026-09-27 14:50 UTC

### Containerd cleanup on w1

Authenticated sudo used a clipboard-sourced password over SSH; no credential was printed or saved. Before cleanup, `df -B1 /` showed 12,333,043,712 bytes available (85% used) and `/var/lib/k0s/containerd` occupied 12G. `k0s ctr containers prune` is **not supported** (`No help topic for prune`), and `k0s crictl` is not available. `k0s ctr -n k8s.io images prune --all` completed successfully. The `/var/lib/k0s/containerd/tmp` directory had no files to remove. Immediately after cleanup, free bytes were unchanged: **0 bytes measurably reclaimed**; containerd remained 12G. No live snapshots or Longhorn data were removed.

### Serial Longhorn expansion

The 12 production volumes began healthy with one replica each. The separate pre-existing `verify-restore-20260927` scratch volume was `unknown` and was not changed. w1 Longhorn reported 13,212,057,600 / 80,846,258,176 bytes available (16.34%); w2 reported 39,636,172,800 / 80,847,286,272 (49.03%). The physical root filesystem on w1 had only 12,333,043,712 / 80,846,258,176 bytes free (15.25%).

| Volume / PVC | Size | Replicas | Replica nodes | Robustness |
|---|---:|---:|---|---|
| `pvc-ad0c149d-6eee-4ff7-9406-7daa57d25fbc` / `commafeed-k8s/commafeed-data-pvc` | 256 MiB | 1 → **2** | **w1, w2**, both running | **healthy** after rebuild (observed degraded during rebuild) |

The patch finished and `kubectl wait --for=jsonpath='{.status.robustness}'=healthy` succeeded. Both replica CRs show `running`. Post-rebuild Longhorn available ratios were still 16.34% (w1) and 49.03% (w2); w1 root free space was 12,330,721,280 / 80,846,258,176 bytes (15.25%). **Stopped after this one volume:** the root free-space margin over 15% is only about 203 MiB; even another 256 MiB provisioned replica could exceed that margin. The other 11 production volumes remain at one replica. Reassess headroom after worker disk expansion or safe, measurable reclamation; do not chain further rebuilds on this margin.

**40 GiB Nextcloud hold:** `pvc-67af9edd-bd28-434b-9adf-a95cb39b99fb` (`nextcloud-k8s/nextcloud-nextcloud`) remains **one replica**, intentionally deferred until external worker disk capacity is added. Its observed actual size was about 46 GiB. Never increase this volume to 2 on the present disks.

### DNS upstream configuration

Before change, each node's generated `/etc/resolv.conf` advertised five upstreams: three from `/etc/systemd/resolved.conf` (`1.1.1.1 9.9.9.9 8.8.8.8`) and two DHCP servers (`185.12.64.1`, `185.12.64.2`) from NetworkManager. On **m1, then w1, then w2**, saved `/etc/systemd/resolved.conf.phase4-backup`, scheduled a 120-second automatic rollback, changed only the persistent global `DNS=` line to `DNS=1.1.1.1`, and restarted systemd-resolved. After each node's public (`example.com`) and internal (`w1`, `w2`, or `m1`) resolver queries passed, the rollback timer was stopped. No interface, generated resolv.conf, Tailscale split DNS, node drain, or reboot was changed.

Each node now advertises **exactly three** upstream nameservers (`1.1.1.1` and both DHCP servers), and systemd-resolved is active. DNSConfigForming had 21 retained event objects with cumulative count **306,511** and latest timestamp **14:50:04 UTC** in the initial post-change snapshot, up from 306,443 at 14:46:57 UTC in the pre-change window. These warnings may include events before all nodes were updated. A repeat at **14:52:40 UTC** stayed at count **306,511**, latest **14:50:04 UTC**: no new warning was observed over this ~2.5-minute window. Keep monitoring over 24–48 hours before claiming durable resolution; old objects persist.


### Forgejo application-only clean dump and isolated replay — final verification

The requested database-scoped archive was created from the CNPG PostgreSQL container with `pg_dump -U postgres --clean --if-exists -Fc forgejo` as `./forgejo_db_clean_2026-09-27.dump` (**476,395 bytes, mode 0600**, SHA-256 `7f0b1fbda3efa8ffc65ffa56450cc12307e6f8ae31615dc13acfe6e2421c9500`). It contains sensitive application data: do not commit or publish it. `pg_restore --list` read the archive successfully. For custom-format archives, the clean/drop directives must also be supplied to **pg_restore**; they are not a substitute for restore-time options.

Validation used a separate PostgreSQL 18.1 container with a local Unix socket and disposable storage, pinned to **w2**. A fresh `forgejo` scratch database was restored with:

```bash
pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl -U postgres -d forgejo /tmp/forgejo.dump
```

**First replay exit 0; second replay into the already populated scratch database exit 0, with no bootstrap-role collision errors.** The second pass is the direct clean/drop replay test. Scratch verification query returned `128|17|10` for public base tables, repositories and users. The earlier independent fresh cluster-wide `pg_dumpall` replay in `/tmp/forgejo-phase4-results.md` also matched 128 tables, 17 repositories, 10 issues and 10 users, but needed bootstrap-role normalization and a two-part continuation; it is **not** the clean database-scoped archive test. The scratch pods were deleted and confirmed absent. Neither logical test validates CNPG PITR, Forgejo file attachments, or all application data.

**Capacity incident during validation:** The first disposable PostgreSQL pod was accidentally scheduled on w1. While it ran, `df -B1 /` fell to **11,950,780,416 bytes free (14.78%)**, below the 15% hold threshold. The pod was deleted, the optional image prune repeated, and all further scratch work moved to w2. At the final check w1 had **12,305,006,592 bytes free (15.22%)**; Longhorn still reported w1 16.34% available and w2 48.12%. No further replica scaling occurred after the threshold breach. The brief w1 dip reinforces the stop on the remaining 11 production volumes; add disk capacity before resuming.

At **17:00 UTC**, `kubectl get events -A --field-selector reason=DNSConfigForming -o json` returned **zero retained event objects**. This is consistent with cessation after the DNS change, but Kubernetes events can expire; continue checking for *new* warnings over the next 24–48 hours. The Commafeed volume remained `healthy` at 2 replicas; the 40 GiB Nextcloud volume remained `healthy` at **1**.
