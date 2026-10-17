#!/usr/bin/env python3
"""Calendar-month retention; fail closed. No object-store or replica-file access."""
import argparse
import calendar
import datetime as D
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from safety import stamp, reasons as local_reasons, classify

ROOT = "/apis/longhorn.io/v1beta2/namespaces/longhorn-system/"
PREFIX = "storage.valentinus.dev/"
KINDS = {"Volume": "volumes", "Snapshot": "snapshots", "Backup": "backups",
         "BackupVolume": "backupvolumes", "BackupTarget": "backuptargets",
         "Engine": "engines", "Replica": "replicas", "Node": "nodes",
         "Setting": "settings", "SnapshotGroup": "snapshotgroups", "RecurringJob": "recurringjobs"}

def month_before(now):
    local = now.astimezone(ZoneInfo("Europe/Istanbul"))
    year, month = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
    return local.replace(year=year, month=month, day=min(local.day, calendar.monthrange(year, month)[1]))

def annotations(obj):
    return obj.get("metadata", {}).get("annotations") or {}

def protected(obj):
    labels = {**(obj.get("metadata", {}).get("labels") or {}),
              **(obj.get("spec", {}).get("labels") or {}),
              **(obj.get("status", {}).get("labels") or {})}
    allowed = {"longhornvolume", "backup-volume", "backup-target", "KubernetesStatus", "longhorn.io/volume-access-mode"}
    return (any(k not in allowed for k in labels) or
            any(any(word in k.lower() for word in ("hold", "protect", "recovery", "retain")) for k in annotations(obj) if k not in {PREFIX + "retention", PREFIX + "recovery-unit", PREFIX + "retention-suspended"}) or
            obj["metadata"]["name"].startswith(("migration-", "manual-", "phase1-", "retry-", "nextcloud-", "synthetic-")))

def owner(volume, inventory):
    k = volume.get("status", {}).get("kubernetesStatus", {})
    matches = [p for p in inventory.get("PersistentVolumeClaim", []) if p["metadata"]["namespace"] == k.get("namespace") and
               p["metadata"]["name"] == k.get("pvcName") and p["spec"].get("volumeName") == volume["metadata"]["name"] and
               p.get("status", {}).get("phase") == "Bound"]
    return matches[0] if len(matches) == 1 else None

def decisions(inv, config, now):
    cutoff = month_before(now)
    vols = {v["metadata"]["name"]: v for v in inv.get("Volume", [])}
    settings = {s["metadata"]["name"]: s.get("value") for s in inv.get("Setting", []) if s.get("status", {}).get("applied")}
    global_why = []
    if inv.get("clusterUID") != config.get("clusterUID") or not config.get("clusterUID"):
        global_why.append("cluster identity mismatch/unknown")
    if settings.get("current-longhorn-version") != "v1.13.0":
        global_why.append("unreviewed Longhorn version")
    if inv.get("SnapshotGroup") or inv.get("externalSnapshotAPI"):
        global_why.append("group/CSI ownership mapping required; preserve all")
    if inv.get("RecurringJob"):
        global_why.append("native recurring job exists; conflict review required")
    for kind in ("Snapshot", "Backup"):
        for obj in inv.get(kind, []):
            m, st = obj["metadata"], obj.get("status", {})
            volname = obj.get("spec", {}).get("volume") if kind == "Snapshot" else st.get("volumeName")
            v = vols.get(volname)
            why = list(global_why)
            created = stamp(st.get("creationTime" if kind == "Snapshot" else "backupCreatedAt"))
            pvc = owner(v, inv) if v else None
            a = annotations(pvc or {})
            app = (pvc["metadata"]["namespace"] + "/" + pvc["metadata"]["name"]) if pvc else None
            if pvc and pvc["metadata"]["namespace"] in config.get("namespaceHolds", {}):
                why.append("explicit namespace hold: " + config["namespaceHolds"][pvc["metadata"]["namespace"]])
            if not pvc or st.get("volumeName", volname) != volname:
                why.append("ownership uncertain")
            if a.get(PREFIX + "retention") != "calendar-month":
                why.append("PVC not explicitly enrolled")
            # Only independently recoverable PVCs can be deleted automatically.
            # Coordinated applications/sets are preserved AS A UNIT, not split.
            if a.get(PREFIX + "recovery-unit") != "standalone":
                why.append("coordinated/unknown recovery unit: preserve entire unit")
            if a.get(PREFIX + "retention-suspended", "false") != "false" or protected(pvc or {"metadata": {"name": ""}}) or (v and protected(v)):
                why.append("application/volume hold or suspension")
            if protected(obj):
                why.append("protected checkpoint/labels/annotations")
            if m.get("deletionTimestamp"):
                why.append("already deleting")
            if created is None or created > now:
                why.append("authoritative timestamp uncertain")
            elif created >= cutoff:
                why.append("within calendar month")
            # Keep latest completed candidate AND latest explicitly restore-tested point.
            peers = [p for p in inv.get(kind, []) if
                     (p.get("spec", {}).get("volume") if kind == "Snapshot" else p.get("status", {}).get("volumeName")) == volname and
                     (p.get("status", {}).get("readyToUse") and not p.get("status", {}).get("markRemoved") if kind == "Snapshot" else p.get("status", {}).get("state") == "Completed") and
                     stamp(p.get("status", {}).get("creationTime" if kind == "Snapshot" else "backupCreatedAt"))]
            field = "creationTime" if kind == "Snapshot" else "backupCreatedAt"
            for subset, label in ((peers, "latest completed candidate; restore usability not inferred"),
                                  ([p for p in peers if annotations(p).get(PREFIX + "restore-evidence")], "latest restore-evidence point")):
                if subset and created == max(stamp(p["status"][field]) for p in subset):
                    why.append(label)
            if not peers:
                why.append("no completed recovery candidate")
            if v:
                # Shared operation gates apply to both kinds. Remote deletion does
                # not need a source merge, so exclude local-only graph/disk gates.
                gates = local_reasons(v, inv.get("Snapshot", []), inv.get("Replica", []), inv.get("Engine", []), inv.get("Node", []), now)
                local_only = ("pre-existing removed chain", "engine/CR", "non-user-created", "replica disk merge headroom")
                why.extend(gates if kind == "Snapshot" else [g for g in gates if not g.startswith(local_only)])
            if kind == "Snapshot" and v:
                why.extend(classify(v, obj, inv.get("Snapshot", []), inv.get("Backup", []), now, cutoff))
                # Current shared Btrfs pool has no proved transient merge bound.
                # Local writes stay disabled until a reviewed implementation can
                # establish actual per-purge headroom, not just st_blocks estimates.
                why.append("local purge disabled: physical merge headroom unproven")
            if kind == "Backup":
                if st.get("state") != "Completed" or st.get("progress") != 100 or st.get("error") or st.get("messages"):
                    why.append("backup not unambiguously completed")
                names = {x for x in (st.get("volumeName"), (m.get("labels") or {}).get("backup-volume"), obj.get("spec", {}).get("volumeName")) if x}
                if names != {volname}:
                    why.append("backup volume identity mismatch")
                if not st.get("snapshotName") or obj.get("spec", {}).get("snapshotName") != st.get("snapshotName"):
                    why.append("backup snapshot identity mismatch/unknown")
                target = st.get("backupTargetName")
                targets = [t for t in inv.get("BackupTarget", []) if t["metadata"]["name"] == target and t.get("status", {}).get("available")]
                attestation = config.get("targets", {}).get(target, {})
                if len(targets) != 1 or attestation.get("url") != targets[0].get("spec", {}).get("backupTargetURL") or not attestation.get("exclusiveClusterEvidence"):
                    why.append("target sharing/exclusive ownership unestablished")
                if settings.get("auto-cleanup-when-delete-backup") != "false":
                    why.append("remote delete could trigger unreviewed local snapshot purge")
                owners = [b for b in inv.get("BackupVolume", []) if b["metadata"]["uid"] in {r.get("uid") for r in m.get("ownerReferences", [])} and b.get("spec", {}).get("volumeName") == volname]
                if len(owners) != 1 or any(b.get("status", {}).get(k) for b in owners for k in ("linkedCloneSourceSnapshot", "linkedCloneSourceVolume")):
                    why.append("backup volume ownership/clone dependency uncertain")
                # Retain exact restore inputs, including completed restored volumes.
                # Do not confuse a retained fromBackup pointer with an active restore.
                for x in inv.get("Volume", []):
                    source = x.get("spec", {}).get("fromBackup")
                    if x.get("status", {}).get("restoreRequired"):
                        why.append("restore input in use/uncertain")
                    if source:
                        query = urllib.parse.parse_qs(urllib.parse.urlparse(source).query)
                        if len(query.get("backup", [])) != 1 or len(query.get("volume", [])) != 1:
                            why.append("restore source ownership uncertain")
                        elif query["backup"] == [m["name"]] and query["volume"] == [volname]:
                            why.append("referenced restore input preserved")
                if any(b.get("status", {}).get("state") != "Completed" or b["metadata"].get("deletionTimestamp") for b in inv.get("Backup", [])):
                    why.append("backup/delete operation active/uncertain")
            yield {"kind": kind, "id": m["name"], "uid": m["uid"], "resourceVersion": m["resourceVersion"],
                   "volume": volname, "application": app, "timestamp": st.get(field),
                   "location": "local" if kind == "Snapshot" else "remote",
                   "recoveryUnit": a.get(PREFIX + "recovery-unit", "unknown"),
                   "recoverySet": st.get("labels", {}).get("recovery-set") if st.get("labels") else None,
                   "eligible": not why, "reasons": sorted(set(why)) or ["old, owned, unheld, standalone; minimum coverage and operation checks passed"]}

class Client:
    def __init__(self):
        sa = "/var/run/secrets/kubernetes.io/serviceaccount/"
        self.token = open(sa + "token").read().strip()
        self.context = ssl.create_default_context(cafile=sa + "ca.crt")
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        self.base = "https://" + ("[" + host + "]" if ":" in host else host) + ":" + os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    def api(self, path, method="GET", body=None):
        req = urllib.request.Request(self.base + path, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            raise RuntimeError("API %s %s HTTP %s" % (method, path, e.code)) from None

    def objects(self, path):
        response = self.api(path + "?limit=500")
        if response is None or response.get("metadata", {}).get("continue"):
            raise RuntimeError("missing/truncated inventory: " + path)
        return response["items"]

    def state(self):
        inv = {kind: self.objects(ROOT + plural) for kind, plural in KINDS.items()}
        inv["PersistentVolumeClaim"] = self.objects("/api/v1/persistentvolumeclaims")
        inv["clusterUID"] = self.api("/api/v1/namespaces/kube-system")["metadata"]["uid"]
        inv["externalSnapshotAPI"] = any(self.api("/apis/" + group) is not None for group in ("snapshot.storage.k8s.io", "groupsnapshot.storage.k8s.io"))
        return inv

def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inventory", help="read-only saved API inventory; never permits apply")
    p.add_argument("--config", default="/etc/retention/policy.json")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.inventory and args.apply:
        p.error("apply requires fresh in-cluster API state")
    config = json.load(open(args.config))
    client = None if args.inventory else Client()
    now = D.datetime.now(D.timezone.utc)
    inv = json.load(open(args.inventory)) if args.inventory else client.state()
    plan = list(decisions(inv, config, now))
    emit("start", at=now.astimezone(ZoneInfo("Europe/Istanbul")).isoformat(), cutoff=month_before(now).isoformat(), apply=args.apply)
    for decision in plan:
        emit("decision", **decision)
    candidates = [d for d in plan if d["eligible"]]
    if not args.apply or not candidates:
        emit("done", candidates=len(candidates), deleted=0)
        return
    if config.get("suspended", True):
        raise RuntimeError("retention suspended; separate reviewed activation required")
    # Pre-created named lease. Never steal an expired lease: an interrupted API
    # delete can still be running. Operator must reconcile it before clearing.
    lockpath = "/apis/coordination.k8s.io/v1/namespaces/longhorn-system/leases/longhorn-snapshot-retention"
    lease = client.api(lockpath)
    if not lease or lease.get("spec", {}).get("holderIdentity"):
        raise RuntimeError("lock held/missing; inspect prior operation, do not overlap")
    lease["spec"] = {"holderIdentity": os.environ["HOSTNAME"], "acquireTime": now.isoformat().replace("+00:00", "Z")}
    lease = client.api(lockpath, "PUT", lease)
    # Exactly one DELETE per run. Persist lock on any uncertain write or result.
    candidate = candidates[0]
    fresh = client.state()
    if json.load(open(args.config)) != config:
        raise RuntimeError("policy changed before mutation; lock retained")
    live = next((d for d in decisions(fresh, config, D.datetime.now(D.timezone.utc)) if d["kind"] == candidate["kind"] and d["id"] == candidate["id"]), None)
    if not live or not live["eligible"] or live["uid"] != candidate["uid"]:
        emit("skip_changed", id=candidate["id"])
    else:
        path = ROOT + KINDS[live["kind"]] + "/" + urllib.parse.quote(live["id"], safe="")
        client.api(path, "DELETE", {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": live["uid"], "resourceVersion": live["resourceVersion"]}})
        emit("delete_requested", **live)
        deadline = time.monotonic() + 120
        baseline = {v["metadata"]["name"]: (v["status"].get("robustness"), v["status"].get("state")) for v in fresh["Volume"]}
        while time.monotonic() < deadline:
            current = client.state()
            if {v["metadata"]["name"]: (v["status"].get("robustness"), v["status"].get("state")) for v in current["Volume"]} != baseline:
                raise RuntimeError("volume health/state changed; lock retained")
            if client.api(path) is None:
                emit("delete_complete", id=live["id"], physicalReclamation="unproven")
                break
            time.sleep(5)
        else:
            raise RuntimeError("delete completion unproven after 120s; lock retained, inspect before next write")
    lease["spec"] = {}
    client.api(lockpath, "PUT", lease)
    emit("done", requests=int(bool(live and live["eligible"])))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("fatal", error=str(exc))
        raise SystemExit(1)
