# Longhorn calendar-month cleanup (review required)

Custom code prepared by the OpenAI assistant through the existing **Shark** account.
This is not a Renovate-generated policy. See `assessment-20261017.md` for the no-op
cleanup result. It is separate from canonical Forgejo upgrade PR #22.

## Status and limits

**Not active.** Merge/reconciliation adopts the existing
`longhorn-system/longhorn-snapshot-retention` name, not a second cleaner. Both the
CronJob and `policy.json` stay suspended. Do not resume its earlier 720h code.
Activation needs another explicit reviewed change; this PR does not grant it.
No native RecurringJob exists now; if one appears the planner blocks until reviewed.

Only remote Longhorn Backup CR deletion is implemented for apply mode. Local
Snapshot CRs are inventoried with conservative chain/operation/coverage checks,
but **all local purge requests stay disabled in code AND RBAC**. Actual physical
merge headroom on the shared Btrfs pool cannot be established from Longhorn CR
sizes; the previous formula was a heuristic, not proof. A reviewed per-chain,
per-replica physical-space admission implementation is required before supporting
local writes. Do not bypass this guard or reuse the old broad purge script.
Coordinated application/set deletion is also intentionally unsupported: preserve
all members instead of making partially deleted recovery sets.

## Semantics and protection contract

`Europe/Istanbul` execution time minus **one calendar month**; same local clock,
last-day clamping (Mar31 → Feb28/29). Strictly older only; equal cutoff is retained.
Installed Longhorn v1.13.0 has age-based `retainAge`, but only Go durations, not
calendar-month arithmetic or these recovery checks. `retain: 30` is a count.
Sources: installed `recurringjobs.longhorn.io` CRD, and upstream v1.13.0
[`backup_controller.go`](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/backup_controller.go),
[`snapshot_controller.go`](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/snapshot_controller.go).
The DELETE backup path can auto-delete its local Snapshot: require applied
`auto-cleanup-when-delete-backup=false` and recheck before every write. Do not change
that setting to make this policy pass.

Ownership is resolved from Longhorn volume → current Bound PVC, not name prefixes
alone. No volume IDs are permanently enrolled. Owners must declare on each PVC,
in that PVC's authoritative GitOps source:

```yaml
metadata:
  annotations:
    storage.valentinus.dev/retention: calendar-month
    storage.valentinus.dev/recovery-unit: standalone
    storage.valentinus.dev/retention-suspended: "false"
```

`standalone` is an owner assertion of independent recoverability. Never put it on
one half of a DB/files/config set. Any other/missing unit preserves the whole
application unit. Any recovery-set/purpose/unknown labels, hold/protection
annotations, system/recovery names, or unresolved controller ownership also preserve
the item. Existing migration/manual/phase1 checkpoints remain protected even without
an explicit hold label. CSI snapshot/group APIs or Longhorn SnapshotGroups fail
closed until ownership/group lifecycle mapping is implemented. No finalizer removal.

Add `storage.valentinus.dev/hold: <reason>` to a PVC, Volume or backup/snapshot to
protect it; existing protection metadata is honored without requiring migration.
A PVC hold/suspension applies to both its local and remote points. `namespaceHolds`
in the policy provides reviewable application-wide holds, currently Forgejo prep.
Never automatically expire a hold. Declare set membership with a `recovery-set`
label on all members; this policy preserves every such member. It does not try to
prove coherence from close timestamps or split DB/data/config units.

Retain latest completed local candidate and remote candidate per volume, even if
all points are old; preserve tied timestamps. A completed candidate is NOT called
restore-verified. A restore-tested item may carry
`storage.valentinus.dev/restore-evidence: <immutable report reference>`; the latest
such point is also preserved. The script honors the claim conservatively but does
not independently verify that report. Failed/incomplete/unknown timestamps and
active backup/restore/rebuild/clone/expansion/purge/unstable replicas fail closed.

Remote deletion additionally requires reviewable `policy.json.targets.<target>`:
`url` must match current BackupTarget URL; `exclusiveClusterEvidence` must reference
owner-approved evidence of exclusive cluster ownership of that Longhorn target
namespace. Bind that evidence to this policy's `clusterUID`. Empty default means
**no remote deletion**. If another cluster shares the target, do not add an
exclusivity assertion; keep deletion disabled until a reviewed cross-cluster
ownership/dependency protocol exists. Sharing among apps does not authorize
bucket-wide expiration. Unknown/orphan backup volume ownership is always skipped.
Never use a generic Garage lifecycle policy for Longhorn shared blocks.

## Operation

Daily 03:00 Europe/Istanbul, `Forbid`, 300s start deadline, 600s Job deadline,
backoff 0. **At most one remote DELETE per run**, with fresh full inventory,
policy-file comparison and UID/resourceVersion preconditions just before mutation.
No retry on uncertain DELETE. Stop on errors or volume-health/state changes.
Completion observation is bounded to 120s; CR absence proves only controller
completion, NOT immediate physical remote reclamation. Other runs wait for next
schedule; no indefinite polling. Logs record IDs, cutoff and concise reasons.

A named Lease also excludes manually started apply Jobs. Never steal a stale
lease: a timed-out Kubernetes request may still have initiated remote work. Errors
retain the lease for operator inspection. Before manually clearing it, prove the
prior Job ended, inspect Backup CR/finalizer and target state, and check all engine
operations/health. Flux `ssa: IfNotPresent` prevents reconciliation from clearing
an in-flight lease. Do not delete/recreate it to unblock a cleaner blindly.

RBAC: Longhorn namespace inventory reads, Backup DELETE only; named Lease get/update;
cluster-wide PVC list for ownership and get of kube-system UID. No secrets, S3
credentials, hostPath, replica-file access, Snapshot DELETE or direct engine purge.
Pinned non-root Python image with limits and TLS-verified in-cluster API. IANA
Europe/Istanbul tzfile is included as a binary ConfigMap file, so the pinned Alpine
image needs no runtime tzdata download; update it when timezone rules change.

## Validation / dry run

```sh
uv run --no-project --python 3.12 python -m unittest discover -s longhorn/calendar-retention
kubectl kustomize flux-system/layers/l3-storage
kubectl apply --dry-run=server -k longhorn/calendar-retention
```

Read-only saved-current-inventory mode:
`uv run --no-project --python 3.12 python longhorn/calendar-retention/retention.py --inventory <private-inventory.json> --config longhorn/calendar-retention/policy.json`.
Inventory is a JSON dictionary keyed by Longhorn Kind, plus
`PersistentVolumeClaim` list, `clusterUID`, and `externalSnapshotAPI` discovery result.
This mode rejects `--apply`; no kubeconfig or credentials are needed.
For an eventual in-cluster dry-run Job, copy the reconciled CronJob template,
remove `--apply`, and use a unique Job name. It only reads APIs and emits decisions.
Do not create it from the old live template before this PR is reconciled.

No deployment command here: delivery is through existing `l3-storage` Flux layer.
To stop future runs, suspend the existing CronJob and policy, and inspect any
already-running Job rather than interrupting a Longhorn operation. Revert the
policy PR to roll back config, but leave suspension in force and inspect the
Lease first. Config rollback cannot restore deleted recovery points.
