"""Conservative v1 engine gates reused from the reviewed 2026-10-14 evidence.
Headroom formula is a heuristic, not a proved Btrfs merge bound.
"""
import datetime as D

def stamp(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        t = D.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t if t.tzinfo and t.utcoffset() == D.timedelta(0) else None
    except ValueError:
        return None

def reasons(v, snaps, replicas, engines, nodes, now):
    why = []
    spec, status = v["spec"], v["status"]
    name = v["metadata"]["name"]
    # v1 Snapshot CR deletion invokes Longhorn purge, which can merge OTHER
    # previously marked-removed snapshots in the same chain. Never initiate
    # that purge while any such record exists outside this deletion request.
    if any(s["spec"].get("volume") == name and s["status"].get("markRemoved") for s in snaps):
        why.append("pre-existing removed chain; controller purge could touch it")
    if spec.get("dataEngine") != "v1" or status.get("state") != "attached" or status.get("robustness") != "healthy":
        why.append("not healthy attached v1")
    if status.get("restoreRequired") or status.get("expansionRequired") or status.get("isStandby") or spec.get("migrationNodeID") or status.get("switchoverState"):
        why.append("volume operation")
    clone = status.get("cloneStatus") or {}
    if clone.get("state") or clone.get("snapshot") or clone.get("sourceVolume"):
        why.append("clone operation")
    salvage_text = status.get("lastAutoSalvagedAt")
    salvaged = stamp(salvage_text)
    if salvage_text and (salvaged is None or salvaged > now):
        why.append("auto-salvage timestamp uncertain")
    elif salvaged and now - salvaged < D.timedelta(hours=24):
        # Site-specific stabilization policy after the multi-volume replica
        # failures, not a Longhorn-required 24-hour wait.
        why.append("auto-salvaged within 24h")
    if not status.get("kubernetesStatus", {}).get("pvName") or status.get("kubernetesStatus", {}).get("pvStatus") != "Bound":
        why.append("PV state uncertain")
    ers = [e for e in engines if e["spec"].get("volumeName") == name]
    if len(ers) != 1 or ers[0]["status"].get("currentState") != "running" or ers[0]["status"].get("isExpanding"):
        why.append("engine state uncertain")
    else:
        es = ers[0]["status"]
        if es.get("backupStatus") or es.get("rebuildStatus") or any(p.get("isPurging") or p.get("error") or p.get("state") not in ("", None, "complete") for p in (es.get("purgeStatus") or {}).values()):
            why.append("backup/rebuild/purge active or uncertain")
        if any(p.get("isRestoring") or p.get("currentRestoringBackup") or p.get("state") not in ("", None) for p in (es.get("restoreStatus") or {}).values()):
            why.append("restore active or uncertain")
        if not es.get("purgeStatus"):
            why.append("purge state unavailable")
        # Engine metadata is authoritative for chain members. A snapshot may
        # fail Kubernetes CR creation (e.g. legacy uppercase manual IDs) while
        # still protecting a restore/backup point in the engine chain.
        engine_snapshots = es.get("snapshots")
        cr_snapshots = {s["metadata"]["name"]: s for s in snaps if s["spec"].get("volume") == name}
        if not isinstance(engine_snapshots, dict) or set(engine_snapshots) - {"volume-head"} != set(cr_snapshots):
            why.append("engine/CR snapshot inventory mismatch")
        elif any(not isinstance(engine_snapshots[sn].get("removed"), bool) or
                 engine_snapshots[sn]["removed"] != bool(cr["status"].get("markRemoved"))
                 for sn, cr in cr_snapshots.items()):
            why.append("engine/CR removed state mismatch")
        # The v1 engine's volume-wide purge also auto-marks all snapshots with
        # usercreated=false, even if they were NOT removed when we planned.
        # Treat them as potential collateral; e.g. a recent expansion marker.
        if isinstance(engine_snapshots, dict) and any(
            sn != "volume-head" and item.get("usercreated") is not True and item.get("removed") is False
            for sn, item in engine_snapshots.items()):
            why.append("non-user-created engine snapshot could be purged")
    rr = [r for r in replicas if r["spec"].get("volumeName") == name and r["spec"].get("active")]
    if len(rr) != spec.get("numberOfReplicas") or not rr:
        why.append("replica count uncertain")
    disks = {(n["metadata"]["name"], d.get("diskUUID")): d
             for n in nodes for d in n["status"].get("diskStatus", {}).values()}
    # Replica spec diskID is Longhorn disk UUID.
    for r in rr:
        rs = r["status"]
        if rs.get("currentState") != "running" or not rs.get("started") or r["spec"].get("failedAt") or r["spec"].get("evictionRequested"):
            why.append("replica not stable")
        failed_text = r["spec"].get("lastFailedAt")
        failed = stamp(failed_text)
        if failed_text and (failed is None or failed > now):
            why.append("replica failure timestamp uncertain")
        elif failed and now - failed < D.timedelta(hours=24):
            why.append("replica failed within 24h")
        if ers and (ers[0]["status"].get("replicaModeMap") or {}).get(r["metadata"]["name"]) != "RW":
            why.append("replica not RW")
        disk = disks.get((r["spec"].get("nodeID"), r["spec"].get("diskID")))
        if disk is None:
            why.append("replica disk not found")
            continue
        cond = {c["type"]: c["status"] for c in disk.get("conditions", [])}
        free, maximum = disk.get("storageAvailable"), disk.get("storageMaximum")
        size = int(spec.get("size", 0))
        # Conservative transient merge allowance: twice provisioned size, 2GiB
        # additional working space, AND 10% disk reserve. This is a gate, not
        # a promise of actual reclamation or a universal purge growth bound.
        if cond.get("Ready") != "True" or cond.get("Schedulable") != "True" or not isinstance(free, int) or not isinstance(maximum, int) or size <= 0 or free <= 2 * size + 2 * 2**30 + maximum // 10:
            why.append("replica disk merge headroom insufficient")
    return sorted(set(why))

def classify(v, snapshot, snapshots, backups, now, cutoff):
    z = snapshot["status"]
    m = snapshot["metadata"]
    name = m["name"]
    volume = v["metadata"]["name"]
    reasons = []
    created = stamp(z.get("creationTime"))
    if created is None or created > now or stamp(z.get("requestedTime")) is None:
        reasons.append("untrusted authoritative timestamp")
    elif created >= cutoff:
        reasons.append("within calendar month")
    if z.get("markRemoved") or not z.get("readyToUse"):
        reasons.append("already removed or not ready")
    if name == "volume-head" or name.startswith(("expand-", "migration-", "phase1-", "manual-", "retry-backup-", "nextcloud-full-")):
        reasons.append("system/recovery snapshot")
    if not name.startswith("snap-"):
        reasons.append("unknown snapshot owner")
    labels = {**(m.get("labels") or {}), **(z.get("labels") or {})}
    annotations = m.get("annotations") or {}
    if any(k != "longhornvolume" for k in labels) or any("hold" in k.lower() or "retain" in k.lower() for k in annotations):
        reasons.append("explicit label/hold")
    if len(m.get("ownerReferences", [])) != 1 or m["ownerReferences"][0].get("uid") != v["metadata"].get("uid") or snapshot["spec"].get("volume") != volume:
        reasons.append("ownership uncertain")
    for b in backups:
        # spec is set before status during backup creation. Never rely only on
        # the reconciled status; a new Backup CR may already claim this snapshot.
        bv = {x for x in (b.get("spec", {}).get("volumeName"),
                           b.get("status", {}).get("volumeName"),
                           b.get("metadata", {}).get("labels", {}).get("backup-volume")) if x}
        if len(bv) != 1:
            reasons.append("backup volume ownership uncertain")
            continue  # fail closed for every snapshot until ownership is known
        if volume not in bv:
            continue
        names = {x for x in (b.get("spec", {}).get("snapshotName"),
                              b.get("status", {}).get("snapshotName")) if x}
        if name in names:
            reasons.append("remote backup reference (preserved)")
        if b.get("status", {}).get("state") != "Completed" or not names:
            reasons.append("backup state active/uncertain")
    recoverable = [s for s in snapshots if s["spec"].get("volume") == volume and s["status"].get("readyToUse") and not s["status"].get("markRemoved") and stamp(s["status"].get("creationTime"))]
    if recoverable and name == max(recoverable, key=lambda s: stamp(s["status"]["creationTime"]))["metadata"]["name"]:
        reasons.append("latest completed local candidate; restore not verified")
    if not recoverable:
        reasons.append("no completed local candidate")
    return reasons
