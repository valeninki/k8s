 1. Executive Summary

 Degraded — workloads are serving, but storage resilience needs attention. At the September 27, 2026 check, all three nodes were Ready, the API readiness checks passed, and no pods or PVCs
 were currently failed. Flux reconciliation was healthy.

 The main risks are:

 - w1 cannot schedule new Longhorn replicas. Its root filesystem, which also holds Longhorn data, is 87% full. Kubelet has repeatedly failed image garbage collection.
 - All 12 Longhorn volumes are healthy but have only one replica. An additional storage fault could cause data loss. w1 logged ext4 journal and I/O errors on Longhorn-attached devices on
 September 24; those errors are historical, and their PVC mapping is not confirmed.
 - Automatic kernel updates fail on m1 and w1 because /boot has only 9.2 MiB free.

 No changes were made.

 2. Node Status

 ┌──────┬───────────────┬─────────────────┬──────────────────┬─────────────────┬─────────────────────────────────────────────────────────┐
 │ Node │ Role          │ OS disk /       │ RAM used / total │ Load 1/5/15 min │ Health                                                  │
 ├──────┼───────────────┼─────────────────┼──────────────────┼─────────────────┼─────────────────────────────────────────────────────────┤
 │ m1   │ Control plane │ 8.3/76 GiB, 12% │ 3.0/7.6 GiB      │ 0.52/0.68/0.97  │ Ready; k0scontroller running; /boot 99% full            │
 ├──────┼───────────────┼─────────────────┼──────────────────┼─────────────────┼─────────────────────────────────────────────────────────┤
 │ w1   │ Worker        │ 65/76 GiB, 87%  │ 3.0/7.6 GiB      │ 0.67/0.68/0.85  │ Ready; k0sworker running; Longhorn disk not schedulable │
 ├──────┼───────────────┼─────────────────┼──────────────────┼─────────────────┼─────────────────────────────────────────────────────────┤
 │ w2   │ Worker        │ 39/76 GiB, 53%  │ 3.2/7.6 GiB      │ 1.40/0.97/1.21  │ Ready; k0sworker running; two failed WireGuard units    │
 └──────┴───────────────┴─────────────────┴──────────────────┴─────────────────┴─────────────────────────────────────────────────────────┘

 All nodes report MemoryPressure=False, DiskPressure=False, and PIDPressure=False. w1 uses 19 MiB of 4 GiB swap; the others use none. /var/lib/k0s and, on workers, /var/lib/longhorn share
 each node’s root filesystem. No /run/reboot-required marker was present; a privileged reboot check was not completed.

 3. Critical / Broken Items

 - Storage capacity and single-copy volumes: w1 has about 11 GiB free, below Longhorn’s 15% scheduling threshold by roughly 278 MB. Its FreeDiskSpaceFailed event has recurred 1,539 times
 over about five days; image garbage collection reports 0 bytes freed. All 12 PVCs are Bound and all 12 Longhorn volumes are attached and healthy, but every volume has
 spec.numberOfReplicas=1. No volume is currently reported degraded. Both worker disks need a capacity review before attempting two replicas: w2 has about 36 GiB free, and a single
 Nextcloud volume is provisioned at 40 GiB.
 - Historical filesystem errors on w1: September 24 kernel logs show I/O faults, ext4 journal aborts, and a “potential data loss” message involving a PostgreSQL inode on several
 Longhorn-attached sd* devices. These device names are transient; the logs do not establish that the host root disk failed or identify the affected PVCs. Current volume health does not
 rule out prior data corruption.
 - Failed OS services: dnf5-automatic.service failed on m1 and w1 because installing kernel 7.2.7 needs another 65 MB in /boot. Both run 7.2.4; w2 runs 7.2.7. On w2, wg-quick@wg0 and
 wg-quick@awg0 fail because their configuration files are absent.

 No failed pods, PVCs, Flux Kustomizations, GitRepositories, or HelmReleases were found. The API /readyz?verbose passed, including etcd. The CNPG forgejo-db cluster is Ready 1/1.

 4. Warnings & Drift

 - Flux is synced, with all eight Kustomizations, the GitRepository, and 14 HelmReleases Ready. No current SOPS or layer-dependency reconciliation error was found. Replica policy
 nevertheless differs: the live Longhorn default and StorageClass specify 3, HelmRelease values specify 2, and existing volumes specify 1.
 - In the last two hours, 21 recurring DNSConfigForming warning objects reported more than three host nameservers. Kubelet retained three. One additional warning was w1 image-disk garbage
 collection.
 - Traefik is Ready, but its pod has six restarts, including an older OOMKilled termination on September 11. Several system pods have higher historical restart counts clustered around
 earlier node events; no current crash loop was found.
 - The wildcard certificate is Ready and expires December 21, 2026; renewal is planned for November 21. There is no pending ACME challenge or failed certificate. Traefik and MetalLB
 controllers are Ready.

 5. Recommended Remediations

 1. Protect data first. Verify Longhorn backups and database recovery, then investigate the September 24 errors before changing replica counts:
     kubectl -n longhorn-system get backups.longhorn.io,backupvolumes.longhorn.io
     kubectl -n longhorn-system get volumes.longhorn.io,replicas.longhorn.io -o wide
     kubectl -n forgejo-k8s describe clusters.postgresql.cnpg.io forgejo-db
   Confirm that a restore has been tested; healthy volumes and continuous archiving alone are not proof of recoverability.
 2. Find and relieve w1 disk use, then add capacity. Use an authorized privileged session to inspect usage:
     sudo btrfs filesystem usage -T /
     sudo du -xhd1 /var/lib/longhorn /var/lib/k0s /var/log
   Remove only confirmed unused data. Do not manually delete Longhorn replica or snapshot directories. Expand worker storage as needed, then verify scheduling with:
     kubectl -n longhorn-system get nodes.longhorn.io -o yaml
     kubectl get events -A --field-selector reason=FreeDiskSpaceFailed
   After backups and enough capacity are confirmed, agree on a replica policy in GitOps and increase existing volumes one at a time, watching robustness after each change:
     kubectl -n longhorn-system patch volumes.longhorn.io <pvc-UUID> \
       --type=merge -p '{"spec":{"numberOfReplicas":2}}'
   Do not apply a changed replica parameter to the existing StorageClass without a migration plan; StorageClass parameters are immutable.
 3. Restore automatic updates on m1 and w1. Each has three installed kernels and a 213 MiB initramfs for the oldest version. On each node, confirm uname -r, review a package-manager
 transaction to remove the unused 7.1.10 kernel, then retry dnf5-automatic.service. Do not manually delete /boot files or remove the running kernel. Schedule any resulting reboot after
 storage and backup checks.
 4. Clear lower-priority warnings. Reduce each node’s upstream resolver list to at most three usable nameservers and verify DNSConfigForming stops. On w2, either restore the missing
 WireGuard configs or disable the two units if those tunnels were intentionally retired. Watch Traefik memory and raise its Git-managed limit only if OOM terminations recur.

 Access note: ~/.ssh/config has m1, w1, and w2 aliases but no HostName entries, so the bare aliases did not resolve here. Live cluster checks succeeded through the local cloud-context;
 node checks used the discovered node addresses and authorized SSH keys.
