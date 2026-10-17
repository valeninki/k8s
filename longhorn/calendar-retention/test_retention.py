import copy
import datetime as D
import unittest
from unittest.mock import patch
import retention as R
from safety import reasons

UTC = D.timezone.utc
NOW = D.datetime(2026, 10, 17, 11, 11, 43, tzinfo=UTC)

def fixture():
    v = {"metadata": {"name": "vol", "uid": "vu"}, "spec": {}, "status": {"kubernetesStatus": {"namespace": "app", "pvcName": "data"}}}
    pvc = {"metadata": {"namespace": "app", "name": "data", "annotations": {R.PREFIX + "retention": "calendar-month", R.PREFIX + "recovery-unit": "standalone"}}, "spec": {"volumeName": "vol"}, "status": {"phase": "Bound"}}
    def backup(name, created):
        return {"metadata": {"name": name, "uid": name, "resourceVersion": "1", "labels": {"backup-volume": "vol"}, "ownerReferences": [{"uid": "bvu"}]}, "spec": {"snapshotName": name}, "status": {"volumeName": "vol", "snapshotName": name, "backupCreatedAt": created, "state": "Completed", "progress": 100, "backupTargetName": "default"}}
    inv = {"clusterUID": "cluster", "Volume": [v], "PersistentVolumeClaim": [pvc], "Backup": [backup("backup-old", "2026-01-01T00:00:00Z"), backup("backup-new", "2026-02-01T00:00:00Z")], "BackupVolume": [{"metadata": {"uid": "bvu"}, "spec": {"volumeName": "vol"}}], "BackupTarget": [{"metadata": {"name": "default"}, "spec": {"backupTargetURL": "s3://test"}, "status": {"available": True}}], "Setting": [{"metadata": {"name": key}, "value": val, "status": {"applied": True}} for key, val in [("current-longhorn-version", "v1.13.0"), ("auto-cleanup-when-delete-backup", "false")]]}
    config = {"clusterUID": "cluster", "targets": {"default": {"url": "s3://test", "exclusiveClusterEvidence": "reviewed evidence reference"}}}
    return inv, config

class PolicyTests(unittest.TestCase):
    def plan(self, inv, config):
        with patch.object(R, "local_reasons", return_value=[]):
            return list(R.decisions(inv, config, NOW))

    def test_calendar_not_30_days(self):
        t = D.datetime(2026, 3, 31, 12, 1, tzinfo=UTC)
        c = R.month_before(t)
        self.assertEqual(c.isoformat(), "2026-02-28T15:01:00+03:00")
        self.assertNotEqual(c, t - D.timedelta(days=30))

    def test_leap_and_year_boundary(self):
        self.assertEqual(R.month_before(D.datetime(2024, 3, 31, tzinfo=UTC)).day, 29)
        self.assertEqual(R.month_before(D.datetime(2026, 1, 15, tzinfo=UTC)).year, 2025)

    def test_minimum_coverage_when_everything_old(self):
        inv, config = fixture()
        old, new = self.plan(inv, config)
        self.assertTrue(old["eligible"])
        self.assertFalse(new["eligible"])
        self.assertIn("latest completed candidate; restore usability not inferred", new["reasons"])

    def test_equal_cutoff_is_not_old(self):
        inv, config = fixture()
        inv["Backup"][0]["status"]["backupCreatedAt"] = "2026-09-17T11:11:43Z"
        self.assertIn("within calendar month", self.plan(inv, config)[0]["reasons"])

    def test_hold_and_set_are_preserved(self):
        inv, config = fixture()
        inv["Backup"][0]["status"]["labels"] = {"recovery-set": "coordinated"}
        self.assertFalse(self.plan(inv, config)[0]["eligible"])
        inv["PersistentVolumeClaim"][0]["metadata"]["annotations"][R.PREFIX + "recovery-unit"] = "app-set"
        self.assertTrue(all(not x["eligible"] for x in self.plan(inv, config)))

    def test_namespace_hold_and_suspension(self):
        inv, config = fixture()
        config["namespaceHolds"] = {"app": "upgrade"}
        self.assertFalse(self.plan(inv, config)[0]["eligible"])
        config.pop("namespaceHolds")
        inv["PersistentVolumeClaim"][0]["metadata"]["annotations"][R.PREFIX + "retention-suspended"] = "true"
        self.assertFalse(self.plan(inv, config)[0]["eligible"])

    def test_unknown_owner_target_or_cluster(self):
        for mutate in (lambda i,c: i.pop("PersistentVolumeClaim"), lambda i,c: c.update(targets={}), lambda i,c: c.update(clusterUID="other")):
            inv, config = fixture()
            mutate(inv, config)
            self.assertFalse(self.plan(inv, config)[0]["eligible"])

    def test_restore_and_backup_conflicts(self):
        inv, config = fixture()
        inv["Volume"][0]["spec"]["fromBackup"] = "some-input"
        self.assertFalse(self.plan(inv, config)[0]["eligible"])
        inv, config = fixture()
        inv["Backup"][1]["status"]["state"] = "InProgress"
        self.assertFalse(self.plan(inv, config)[0]["eligible"])

    def test_completed_restore_pointer_preserves_exact_input(self):
        inv, config = fixture()
        inv["Volume"][0]["spec"]["fromBackup"] = "s3://test/?backup=backup-old&volume=vol"
        self.assertIn("referenced restore input preserved", self.plan(inv, config)[0]["reasons"])
        inv["Volume"][0]["spec"]["fromBackup"] = "s3://test/?backup=backup-new&volume=vol"
        self.assertTrue(self.plan(inv, config)[0]["eligible"])

    def test_remote_delete_local_side_effect(self):
        inv, config = fixture()
        inv["Setting"][1]["value"] = "true"
        self.assertFalse(self.plan(inv, config)[0]["eligible"])

    def test_prior_removed_and_unknown_engine_chain_block_purge(self):
        inv, _ = fixture()
        s = {"metadata": {"name": "old"}, "spec": {"volume": "vol"}, "status": {"markRemoved": True}}
        why = reasons(inv["Volume"][0], [s], [], [], [], NOW)
        self.assertIn("pre-existing removed chain; controller purge could touch it", why)

    def test_csi_and_group_fail_closed(self):
        inv, config = fixture()
        inv["externalSnapshotAPI"] = True
        self.assertTrue(all(not x["eligible"] for x in self.plan(inv, config)))

    def test_latest_restore_evidence_kept_too(self):
        inv, config = fixture()
        inv["Backup"][0]["metadata"]["annotations"] = {R.PREFIX + "restore-evidence": "restore report"}
        self.assertFalse(self.plan(inv, config)[0]["eligible"])

if __name__ == "__main__":
    unittest.main()
