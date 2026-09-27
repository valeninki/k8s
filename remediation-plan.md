# Resilience remediation runbook — 2026-09-27 UTC

**State: planning only. No cluster, node, or GitOps mutation was performed.** Obtain operator approval per phase and use a maintenance/change record. This runbook cannot promise zero downtime: CNPG `forgejo-db` has one instance, Traefik has one pod pinned to m1, and each Longhorn volume has one replica. Do not drain or reboot a node while these single points remain. A third storage node (or equivalent independent failure domain) is needed for **three** node-separated Longhorn replicas; two storage nodes support at most two node-separated copies.

## Observed baseline and blockers

- `cloud-context`: m1/w1/w2 Ready; 8/8 Flux Kustomizations Ready (via `kubectl`, because local `flux` CLI is absent); Traefik 1/1 on m1, six old restarts. Warning event objects: 21 `DNSConfigForming`, one `FreeDiskSpaceFailed` (event counts may be much larger).
- All 12 Longhorn volumes are attached, `healthy`, **one** replica. w1 disk scheduling is False: 11,848,908,800 bytes available versus 12,126,938,726 bytes minimum (15%). w2 is schedulable with 39,426,457,600 bytes available, 15,569,256,448 bytes scheduled and 5 GiB reserved. Scheduled/actual/provisioned sizes differ: do not treat free bytes as guaranteed rebuild capacity. Nextcloud's 40 GiB volume reports ~46 GiB `actualSize` including snapshot/engine overhead. Need additional independent disk capacity before broad rebuilds.
- BackupVolume objects exist for 12/12 volumes and 24 Backup objects exist, but **all latest timestamps are 2026-07-28 ~15:32 UTC**; last sync ~2026-08-20. No Longhorn RecurringJob objects. BackupTarget `default` points to `s3://cluster-backup@garage/`, reports `available=True`, but `pollInterval=0s` and `lastSyncedAt=2026-08-20`; this is not a live recovery proof. They are stale and not positive backup verification. Inspect backup target reachability and perform a fresh, independently restored backup of each volume before replica changes or drains/reboots. Backups of running databases also need application-consistent database recovery.
- CNPG `forgejo-db` Ready 1/1; no CNPG Backup or ScheduledBackup CRs. At 2026-09-27 12:10 UTC `pg_stat_archiver` reported 970 successful archives, last WAL archived 12:09 UTC, 0 failures; data checksums on, **zero replication slots** (expected with one instance). Archiver status alone does not establish a recoverable base backup/PITR chain. Verify actual configured archive destination and restore a database copy outside production. Its live PVC `pvc-799169cf-21e0-4ea0-b9e3-0ea8ceecc9e2` was attached to w2; that **does not** resolve which September 24 w1 sd* device had ext4 errors. Historical attachment/device identity must be correlated separately.
- Live default `longhorn` StorageClass requests 3 replicas; repository `flux-system/layers/l3-storage/longhorn.yaml` Helm default requests 2. All existing volumes request 1. Repository contains extensive unrelated uncommitted changes and staged deletions/replacements under `flux-system/layers/`; do not commit/reconcile from this worktree or edit old class parameters. Legacy `longhorn/application.yml` and `traefik/application.yml` may belong to Argo; confirm live controller ownership before editing. `traefik/application.yml` is **not** proven to manage the current deployment.
- SSH with the configured key reached w2 at 100.100.20.2; key login to m1 (100.100.10.1) and w1 (100.100.20.1) failed. w2 needs sudo authentication. The checkup's node disk and /boot figures for m1/w1 are historical until access is restored. Never embed passwords in CLI arguments, logs, scripts, documents, or shell history.

## Pre-flight: read-only gates

Run locally; stop if namespace, context, node identity, or owner differs. Preserve command output in a restricted operator workspace without secrets.

```sh
kubectl config current-context
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running
kubectl get pvc -A -o wide
kubectl -n longhorn-system get volumes.longhorn.io,replicas.longhorn.io,nodes.longhorn.io -o wide
kubectl -n longhorn-system get backupvolumes.longhorn.io,backups.longhorn.io -o wide
kubectl -n forgejo-k8s get cluster.postgresql.cnpg.io forgejo-db -o yaml
kubectl -n forgejo-k8s get backups.postgresql.cnpg.io,scheduledbackups.postgresql.cnpg.io
kubectl get volumeattachment -o wide
kubectl -n flux-system get kustomizations.kustomize.toolkit.fluxcd.io -o wide
kubectl get storageclass longhorn -o yaml
git status --short
```

Connect to verified node IPs using SSH config identity; if IPs change, resolve aliases with `ssh -G w1 | grep -E '^(hostname|user|identityfile) '` and corroborate with `kubectl get nodes -o wide`. Do not use password arguments or `sshpass`. If a login password is required, use an interactive SSH/TTY prompt and paste the credential from clipboard via desktop paste; if an askpass helper is required, configure its *program* to call `wl-paste --no-newline` (or `xclip -selection clipboard -o`) without recording output. For sudo, use an interactive prompt/desktop paste only; never pipe the clipboard to `sudo -S` through a logged session. If safe interactive auth is unavailable, **stop** and ask the operator to arrange access. Login template:

```sh
ssh -i ~/.ssh/thinkpad_ed25519 -o IdentitiesOnly=yes valen@100.100.20.1
# On node, authenticate interactively if authorized; do not print the clipboard.
sudo -v
hostname; uname -r; df -h / /boot; findmnt / /boot /var/lib/longhorn
```

**Global hold:** No replica-count mutation, drain or reboot before fresh verified Longhorn volume backups **and** a verified CNPG database base backup/PITR or tested logical recovery. No deletion inside `/var/lib/longhorn` or active DB directories, ever. No mounted-volume `fsck`, no volume detach, and no package manager transaction affecting the running kernel. Stop on fresh I/O errors, rebuilding/degraded volumes, PVC failures, unverified recovery, capacity shortage, or loss of API access. For every write: record pre-state, make one change, observe, then continue only if healthy; rollback should not delete the last good copy.

## Phase 1 — establish recoverability and investigate errors

**Read-only audit:**

```sh
kubectl -n longhorn-system get backuptargets.longhorn.io -o yaml
kubectl -n longhorn-system get settings.longhorn.io | grep -E 'backup|replica|storage-minimal'
kubectl -n longhorn-system get backups.longhorn.io -o custom-columns=VOLUME:.spec.volumeName,STATE:.status.state,CREATED:.status.createdAt,URL:.status.url
kubectl -n longhorn-system get backupvolumes.longhorn.io -o custom-columns=NAME:.metadata.name,LATEST:.status.lastBackupAt,SYNC:.status.lastSyncedAt
kubectl -n forgejo-k8s describe cluster.postgresql.cnpg.io forgejo-db
kubectl -n forgejo-k8s exec forgejo-db-1 -- psql -U postgres -d postgres -X -c 'SELECT archived_count,last_archived_wal,last_archived_time,failed_count,last_failed_time FROM pg_stat_archiver;'
kubectl -n forgejo-k8s exec forgejo-db-1 -- psql -U postgres -d postgres -X -c 'SELECT slot_name,slot_type,active,restart_lsn FROM pg_replication_slots;'
kubectl -n forgejo-k8s exec forgejo-db-1 -- psql -U postgres -d postgres -X -c 'SHOW archive_command; SHOW data_checksums; SELECT pg_is_in_recovery();'
kubectl get pvc -A -o wide; kubectl get pv -o wide; kubectl get volumeattachment -o yaml
```

Expected: backup target reachable, every volume has a **recent completed** backup independently visible at the target, no failed archive, and an isolated restore produces valid data. If absent (current state), get explicit authorization for a backup implementation and schedule; validate target credentials without printing them, retention, bucket permissions, free capacity, encrypted copies, and a fresh backup of **all 12** using supported Longhorn backup APIs/UI with one volume at a time. For databases use CNPG-supported base backup/object-store integration or a consistent logical dump **plus** tested restore; align WAL retention and base backup timestamps for PITR. Restore Longhorn backups to **new disposable volumes in an isolated namespace** and verify file/application checksums; restore CNPG to a separate test cluster and run `pg_isready`, a representative query and a consistency check. Never mount a restore over production. Record backup IDs, timestamps, target, recovery point and restore evidence in a secure change record; no secret values. **Rollback:** cancel the new backup job if it harms latency; keep production unchanged. If restore fails, fix the backup chain and repeat before any further mutation.

For September 24 mapping, compare archived w1 kernel journal device serial/major:minor and Longhorn engine/instance logs, volume attachment history/CSI publish records and PVC UUID. Current w1 attached volumes include Nextcloud data/DB and Forgejo shared storage; Forgejo **CNPG** PVC is on w2 now. Device sd* letters alone are not stable evidence. With authorized node access:

```sh
sudo journalctl -k --since '2026-09-24 00:00:00 UTC' --until '2026-09-25 00:00:00 UTC' --no-pager | grep -Ei 'ext4|i/o error|journal abort|sd[a-z]|longhorn'
lsblk -o NAME,MAJ:MIN,SERIAL,WWN,FSTYPE,MOUNTPOINTS
sudo dmesg -T | grep -Ei 'ext4|i/o error|journal abort' | tail -80
```

Take a fresh read-only look at database checksums and logical integrity on a restored clone first. For live PostgreSQL, run `pg_amcheck` only after confirming its version, resource budget and backup; it is read-only but can impose significant I/O. Do **not** run offline `pg_checksums` or `fsck` against mounted production devices. **Gate:** if fresh errors recur, halt cleanups/rebuilds and diagnose provider disk, controller and snapshot path with vendor support. Rollback: stop heavy scans and return to observation.

## Phase 2 — w1 disk pressure, one safe cleanup at a time

After authorized node access, measure usage without following other filesystems:

```sh
ssh -i ~/.ssh/thinkpad_ed25519 valen@100.100.20.1
sudo btrfs filesystem usage -T /
sudo du -xhd1 /var/log /var/lib/k0s /var/lib/docker /var/lib/containerd /var/cache /tmp /var/lib/longhorn 2>&1
sudo journalctl --disk-usage
sudo crictl info
sudo crictl images
```

Confirm container runtime endpoint (k0s may have a nondefault socket) and assess shared image cache with running pods. Approval for limited cleanup: `sudo crictl rmi --prune` **only with the verified runtime endpoint**, then `sudo journalctl --vacuum-time=3d --vacuum-size=500M`. Journal vacuum removes archived logs only; export any needed September 24 evidence first. Avoid manual deletion of images, Longhorn data, snapshots, containerd directories, DB files, and random cache directories. If unused images occupy little, do not force deletion; provision more worker disk capacity instead. After each operation:

```sh
df -h /
sudo btrfs filesystem usage -T /
kubectl -n longhorn-system get nodes.longhorn.io w1 -o jsonpath='{.status.diskStatus}'
kubectl get events -A --field-selector reason=FreeDiskSpaceFailed
```

Expected: root **below 80% used**, Longhorn disk `Ready=True` and `Schedulable=True` persistently, and enough excess for *scheduled* replica size plus rebuild snapshots, reservations and growth, not just the 15% threshold. **Rollback:** image prune is not reversible; runtime will repull images (check registry availability before pruning). Journal vacuum cannot be undone; retain evidence beforehand. Stop on node pressure, registry failure, or pod restarts; add capacity rather than deleting active data.

## Phase 3 — /boot and services (no reboot/drain in this phase)

On **each** m1 and w1 after SSH authentication, review package transaction before approval; old version below is historical, not an exact package NEVRA:

```sh
uname -r; df -h /boot; findmnt /boot
rpm -qa | grep -E '^kernel-(core|modules|modules-core|modules-extra)-' | sort
sudo dnf5 repoquery --installed 'kernel*' | grep -E '7\.1\.10|7\.2\.4|7\.2\.7'
sudo dnf5 remove --assumeno 'kernel-core-7.1.10*' 'kernel-modules-7.1.10*' 'kernel-modules-core-7.1.10*' 'kernel-modules-extra-7.1.10*'
```

If and only if the dry run shows **only** obsolete 7.1.10 kernel packages (no running kernel, k0s, unrelated dependencies), obtain approval to repeat the exact reviewed `dnf5 remove` without `--assumeno`; use exact installed NEVRAs, not a broad wildcard if it matches anything else. Keep at least one known-good inactive kernel. Do not delete `/boot` files by hand. Validate `df -h /boot` shows **>100 MiB** free, ideally enough for the complete update transaction. Then, after checking timer schedule and change authorization:

```sh
sudo systemctl restart dnf5-automatic.service
systemctl status dnf5-automatic.service --no-pager
systemctl is-enabled dnf5-automatic.timer
systemctl is-active dnf5-automatic.timer
systemctl list-timers dnf5-automatic.timer
sudo journalctl -u dnf5-automatic.service -n 60 --no-pager
```

`dnf5-automatic.service` is generally a **oneshot** and may be `inactive (dead)` after a successful run; require exit `0`/`Result=success` and **active timer**, not `is-active service=active`. Running it may install updates; approve the update transaction and ensure no automatic reboot is configured. **Rollback:** stop timer if it repeatedly fails, investigate transaction history, reinstall prior kernel only if safe; never boot an unverified kernel, and do not reboot in this phase.

On w2, first check tunnel dependency and intended config, then decide; do not disable blindly:

```sh
ssh -i ~/.ssh/thinkpad_ed25519 valen@100.100.20.2
systemctl status wg-quick@wg0 wg-quick@awg0 --no-pager
systemctl is-enabled wg-quick@wg0 wg-quick@awg0
sudo ls -l /etc/wireguard
sudo wg show
```

If owner confirms tunnels retired and alternative admin/API access works, approve `sudo systemctl disable --now wg-quick@wg0 wg-quick@awg0`; optionally mask only after confirming no automation needs them. If required, restore configs from protected source with correct permissions rather than disabling. **Rollback:** if retired choice is wrong, restore config and `sudo systemctl unmask wg-quick@wg0 wg-quick@awg0; sudo systemctl enable --now wg-quick@wg0 wg-quick@awg0`; confirm connectivity. Avoid printing tunnel private keys.

## Phase 4 — fault domains, GitOps and incremental replicas

After **both** backup gates pass and capacity is expanded if needed, inspect actual vs provisioned usage and placement:

```sh
kubectl -n longhorn-system get nodes.longhorn.io -o yaml
kubectl -n longhorn-system get volumes.longhorn.io -o custom-columns=NAME:.metadata.name,SIZE:.spec.size,ACTUAL:.status.actualSize,REPLICAS:.spec.numberOfReplicas,ROBUST:.status.robustness
kubectl -n longhorn-system get replicas.longhorn.io -o custom-columns=VOLUME:.spec.volumeName,NODE:.spec.nodeID,STATE:.status.currentState
kubectl get storageclass longhorn -o yaml
kubectl -n longhorn-system get settings.longhorn.io -o wide | grep -E 'replica-count|storage-minimal|backup-target'
```

Choose **two** as a realistic initial failure-domain target on w1+w2; first supply enough headroom for the 40 GiB Nextcloud provisioned volume and snapshot overhead on its peer. Two replicas on only two workers tolerate one *storage* node loss, but rebuild waits for that node or new capacity; application scheduling, CNPG and Traefik still need separate HA work. Three replicas need a third eligible storage failure domain and adequate disk. `flux-system/layers/l3-storage/storageclass.yaml` currently declares immutable 3. Do **not** modify its `parameters` in place. In a clean Git branch and after owner approval, add a **new named** `longhorn-2` StorageClass via Flux using copied parameters except `numberOfReplicas: "2"`, and switch default annotations deliberately with no overlapping defaults; update explicit `storageClassName` references separately. Preserve old class for old PVCs; avoid accidental pruning. Align `flux-system/layers/l3-storage/longhorn.yaml` default 2 with chosen policy and confirm which controller owns Longhorn. Do not reconcile this dirty worktree or apply legacy Argo manifests. Rollback GitOps policy by reverting the reviewed commit/default annotations; do not delete a class still used by PVCs.

For existing volumes, start a small noncritical PVC; record old spec, attachment, replica node, backups and working workload. Patch **one** volume only after approved gate (replace placeholder; run locally):

```sh
VOLUME=pvc-<verified-uuid>
kubectl -n longhorn-system get volumes.longhorn.io "$VOLUME" -o yaml
kubectl -n longhorn-system patch volumes.longhorn.io "$VOLUME" --type=merge -p '{"spec":{"numberOfReplicas":2}}'
kubectl -n longhorn-system get volumes.longhorn.io "$VOLUME" -w
kubectl -n longhorn-system get replicas.longhorn.io -o wide
kubectl get events -A --field-selector type=Warning --sort-by=.metadata.creationTimestamp
```

Expected: second replica on **other** worker, rebuild completes, spec/current replicas=2, Longhorn robustness=`healthy`, no new errors, workload still Ready, ample free space. Repeat one volume at a time, leaving CNPG and Nextcloud large volume last and monitoring I/O/latency and backups. **Rollback:** on failed rebuild, **stop and diagnose capacity/network/disk**; never blindly patch back to 1 or delete the only good replica. If second copy is verified good but causes pressure, coordinate Longhorn-supported replica reduction after recovery gate and check PVC health. No node drains/reboots until replicas are healthy, backups remain verified, CNPG has a tested failover/restore plan and workload PDBs allow disruption.

## Phase 5 — DNS, ingress and final verification

Read-only resolver diagnosis on each node: `resolvectl status; cat /etc/resolv.conf; readlink -f /etc/resolv.conf; nmcli device show | grep -E 'IP[46]\.DNS|GENERAL.DEVICE'`. Identify source (NetworkManager, systemd-resolved, DHCP/Tailscale); do **not** overwrite a generated `/etc/resolv.conf` blindly. Change only the upstream source to advertise <=3 working nameservers, one node at a time with authorized network fallback; check node DNS and API reachability immediately. **Rollback:** restore the original network profile/config from the change record and reconnect via out-of-band console if needed.

Inspect current Traefik owner and memory before tuning: `kubectl -n traefik get deployment traefik -o yaml; kubectl -n traefik top pod; kubectl -n traefik describe pod -l app.kubernetes.io/name=traefik`; compare 7–30 day memory peaks to live requests/limits. Change the **actual owning** Flux HelmRelease (not assumed legacy `traefik/application.yml`) in a clean branch only if OOM trend warrants. A singleton rollout can cause ingress downtime: add redundant eligible replicas/placement and test hostPort/service topology first. **Rollback:** revert Git commit/reconcile owner if memory or ingress gets worse; keep previous ReplicaSet until stable.

### Verification matrix (rerun after each phase and after a 24–48 h observation window)

| Objective | Exact check | Pass signal / failure action |
|---|---|---|
| Cluster/pods | `kubectl get nodes -o wide; kubectl get pods -A; kubectl get pvc -A` | 3 Ready, expected replicas Ready, all PVCs Bound; stop on regression. |
| Warning-free **new** events | `kubectl get events -A --field-selector type=Warning --sort-by='.metadata.creationTimestamp'` | No **new** DNSConfigForming, disk, I/O or rebuild warnings during window. Old retained events do not disappear on repair; do not delete events to make output empty. |
| Longhorn | `kubectl -n longhorn-system get volumes.longhorn.io,replicas.longhorn.io -o wide; kubectl -n longhorn-system get nodes.longhorn.io w1 w2 -o yaml` | 12 volumes healthy, target replica count 2 and copies on distinct nodes, both disks Ready/Schedulable, enough reserved space. |
| Backups | `kubectl -n longhorn-system get backupvolumes.longhorn.io,backups.longhorn.io -o wide; kubectl -n forgejo-k8s get cluster.postgresql.cnpg.io forgejo-db -o yaml; kubectl -n forgejo-k8s get backups.postgresql.cnpg.io,scheduledbackups.postgresql.cnpg.io` | Fresh per-volume completed backup + isolated restore evidence; CNPG tested backup/PITR recovery and fresh WAL, not merely CR health. |
| OS boot/update | On m1/w1: `uname -r; df -h /boot; systemctl is-active dnf5-automatic.timer; systemctl list-timers dnf5-automatic.timer; systemctl show dnf5-automatic.service -p Result -p ExecMainStatus` | >100 MiB free, active timer, last service successful; oneshot service need not stay active. |
| Network/ingress | `kubectl -n traefik get deploy,pods -o wide; kubectl -n traefik top pods; kubectl get events -A --field-selector reason=DNSConfigForming` | No fresh DNS warnings or OOM, ingress and DNS probes succeed; old events can remain. |
| GitOps | `kubectl -n flux-system get kustomizations.kustomize.toolkit.fluxcd.io -o wide; kubectl get helmreleases.helm.toolkit.fluxcd.io -A` | All Ready, no immutable StorageClass reconciliation errors. `flux get kustomizations -A` where Flux CLI is installed. |

**Completion criterion:** backups and restores verified, historical disk error mapped or explicitly documented as unresolved with monitoring, pressure cleared, update timers healthy, warnings stop recurring, and all 12 volumes have two healthy node-separated replicas. If m1 Traefik/CNPG remain singleton, state plainly that *full* zero-downtime resilience is **not yet achieved** and schedule their HA design separately.
