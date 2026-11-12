import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from imageforge_mcp import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mount = Path(self.temp.name) / "volume"
        self.mount.mkdir()
        self.patches = [
            patch.object(storage, "MOUNT", self.mount),
            patch.dict(os.environ, {"IMAGEFORGE_RESOURCE_ROOT": str(self.mount / "MCP/resources/v1")}),
            patch.object(storage, "guard_volume"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_commit_hash_and_replay(self):
        directory = storage.new_directory(storage.downloads_root(), "job1")
        path = directory / "frame.png"
        digest = storage.atomic_bytes(path, b"png-data")
        self.assertEqual(digest, hashlib.sha256(b"png-data").hexdigest())
        storage.commit(directory, origin="intermediate", source="input.png")
        self.assertTrue(storage.verified_file(path))
        manifest = json.loads((directory / ".imageforge-manifest.json").read_text())
        self.assertEqual(manifest["files"][0]["sha256"], digest)
        with self.assertRaises(storage.StorageConflict):
            storage.atomic_bytes(path, b"changed")
        self.assertEqual(path.read_bytes(), b"png-data")
        path.write_bytes(b"tampered")
        self.assertFalse(storage.verified_file(path))

    def test_symlink_escape_and_invalid_path(self):
        storage.ensure_directory(storage.service_root())
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (storage.service_root() / "escape").symlink_to(outside)
        with self.assertRaises(storage.StorageUnavailable):
            storage.new_directory(storage.service_root() / "escape", "job")
        with self.assertRaises(storage.StorageUnavailable):
            storage.new_directory(storage.service_root(), "../escape")
        self.assertEqual(list(outside.iterdir()), [])

    def test_concurrent_publish_has_one_winner(self):
        directory = storage.new_directory(storage.downloads_root(), "race")
        results = []
        lock = threading.Lock()

        def write(value):
            try:
                storage.atomic_bytes(directory / "same.png", value)
                outcome = "ok"
            except storage.StorageConflict:
                outcome = "conflict"
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=write, args=(bytes([n]),)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["conflict", "ok"])

    def test_gate_closed_without_identity_before_mkdir(self):
        self.patches[-1].stop()
        try:
            with patch.dict(os.environ, {"IMAGEFORGE_VOLUME_UUID": "",
                                         "IMAGEFORGE_DEVICE_IDENTIFIER": "",
                                         "IMAGEFORGE_EXTERNAL_BUS": ""}):
                with self.assertRaises(storage.StorageUnavailable):
                    storage.new_directory(storage.downloads_root(), "blocked")
            self.assertFalse((self.mount / "MCP").exists())
        finally:
            self.patches.pop()

    def test_wrong_device_and_read_only_fail_before_mkdir(self):
        self.patches[-1].stop()
        self.patches.pop()
        env = {"IMAGEFORGE_VOLUME_UUID": "uuid", "IMAGEFORGE_DEVICE_IDENTIFIER": "disk5s1",
               "IMAGEFORGE_PHYSICAL_STORE": "disk6s1",
               "IMAGEFORGE_EXTERNAL_BUS": "USB"}
        mount = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=10)
        parent_same = SimpleNamespace(st_dev=10)
        parent_other = SimpleNamespace(st_dev=9)
        with patch.dict(os.environ, env):
            with patch.object(storage, "_mount_state", return_value=(mount, parent_same, SimpleNamespace(f_flag=0))):
                with self.assertRaisesRegex(storage.StorageUnavailable, "not mounted"):
                    storage.ensure_directory(storage.service_root())
            with patch.object(storage, "_mount_state", return_value=(mount, parent_other, SimpleNamespace(f_flag=1))):
                with self.assertRaisesRegex(storage.StorageUnavailable, "read only"):
                    storage.ensure_directory(storage.service_root())
            with patch.object(storage, "_mount_state", return_value=(mount, parent_other, SimpleNamespace(f_flag=0))):
                with patch.object(storage, "_disk_info", return_value={"VolumeUUID": "wrong"}):
                    with self.assertRaisesRegex(storage.StorageUnavailable, "identity mismatch"):
                        storage.ensure_directory(storage.service_root())
        self.assertFalse((self.mount / "MCP").exists())

    def test_noowners_does_not_skip_namespace_permission_check(self):
        self.patches[-1].stop()
        self.patches.pop()
        namespace = storage.service_root()
        namespace.mkdir(parents=True)
        storage.shared_root().chmod(0o777)
        device = self.mount.stat().st_dev
        state = (SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=device),
                 SimpleNamespace(st_dev=device + 1), SimpleNamespace(f_flag=0))
        env = {"IMAGEFORGE_VOLUME_UUID": "uuid", "IMAGEFORGE_DEVICE_IDENTIFIER": "disk5s1",
               "IMAGEFORGE_PHYSICAL_STORE": "disk6s1", "IMAGEFORGE_EXTERNAL_BUS": "USB"}
        disk = {"VolumeUUID": "uuid", "DeviceIdentifier": "disk5s1",
                "APFSPhysicalStores": ["disk6s1"]}
        physical = {"DeviceIdentifier": "disk6s1", "BusProtocol": "USB", "Internal": False}
        with patch.dict(os.environ, env), patch.object(storage, "_mount_state", return_value=state), \
             patch.object(storage, "_disk_info", side_effect=lambda path: disk if path == storage.MOUNT else physical), \
             patch.object(storage.subprocess, "run", return_value=SimpleNamespace(
                 stdout=f"/dev/disk5s1 on {self.mount} (apfs, noowners)")):
            with self.assertRaisesRegex(storage.StorageUnavailable, "ownership|mode|ACL"):
                storage.guard_volume()
            with patch.object(storage, "_directory_contract"):
                storage.guard_volume()

    def test_absent_mount_fails_without_local_fallback(self):
        self.patches[-1].stop()
        self.patches.pop()
        env = {"IMAGEFORGE_VOLUME_UUID": "uuid", "IMAGEFORGE_DEVICE_IDENTIFIER": "disk5s1",
               "IMAGEFORGE_PHYSICAL_STORE": "disk6s1", "IMAGEFORGE_EXTERNAL_BUS": "USB"}
        with patch.dict(os.environ, env), patch.object(storage, "_mount_state", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(storage.StorageUnavailable, "absent"):
                storage.new_job("0" * 32, "original")
        self.assertFalse((self.mount / "MCP").exists())

    def test_directory_contract_rejects_wrong_owner_mode_and_acl(self):
        path = self.mount
        device = path.stat().st_dev
        uid, gid = path.stat().st_uid, path.stat().st_gid
        with patch.object(storage, "_has_acl", return_value=False):
            storage._directory_contract(path, uid=uid, gid=gid, mode=0o755, device=device)
            with self.assertRaises(storage.StorageUnavailable):
                storage._directory_contract(path, uid=uid + 1, gid=gid, mode=0o755, device=device)
            with self.assertRaises(storage.StorageUnavailable):
                storage._directory_contract(path, uid=uid, gid=gid, mode=0o700, device=device)
        with patch.object(storage, "_has_acl", return_value=True), self.assertRaises(storage.StorageUnavailable):
            storage._directory_contract(path, uid=uid, gid=gid, mode=0o755, device=device)

    def test_acl_probe_fails_closed_on_acl_and_unreadable_output(self):
        for output in ("drwxr-xr-x+ 2 root wheel 64 Sep 17 00:00 root\n 0: user:staff allow write\n", ""):
            with patch.object(storage.subprocess, "run", return_value=SimpleNamespace(stdout=output)):
                if output:
                    self.assertTrue(storage._has_acl(self.mount))
                else:
                    with self.assertRaises(storage.StorageUnavailable):
                        storage._has_acl(self.mount)


if __name__ == "__main__":
    unittest.main()
