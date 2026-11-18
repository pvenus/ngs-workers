"""Isolated memory DB/ephemeral loopback only; no model or operating service."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from workers.resident.contracts import PROFILES
from workers.resident.adapters.model_residency import ModelResidencyAuthority
from workers.resident.adapters.model_residency_http import HttpModelResidency, handler


class Database:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.now = 100
    def clock(self): return self.now
    @contextmanager
    def tx(self):
        outer = self.db.in_transaction
        if not outer: self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            if not outer: self.db.execute('COMMIT')
        except BaseException:
            if not outer: self.db.execute('ROLLBACK')
            raise


class ResidencyTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.db.close)
        self.unloaded = False
        self.authority = ModelResidencyAuthority(self.db, threading.RLock(), {'mac': 100},
            {'animation': {'host_id': 'mac', 'model_id': 'wan-session', 'memory_bytes': 70},
             'music': {'host_id': 'mac', 'model_id': 'ace-session', 'memory_bytes': 40}},
            verify_unloaded=lambda lease: self.unloaded, lease_seconds=30)
        self.authority.initialize_schema()
    def test_postprocess_alpha_uses_explicit_server_memory_profile(self):
        authority=ModelResidencyAuthority(self.db,threading.RLock(),{'mac':100},
            {'postprocess':{'host_id':'mac','model_id':'alpha','memory_bytes':70}},
            verify_unloaded=lambda lease:self.unloaded)
        authority.initialize_schema()
        lease=authority.acquire(PROFILES['postprocess'],'frames','one')
        self.assertEqual(lease.memory_bytes,70)
        self.assertIsNone(self.acquire())
    def acquire(self, worker='video', key='first'):
        return self.authority.acquire(PROFILES['animation'], worker, key)
    def test_idle_model_holds_budget_after_execution_grant_ends(self):
        lease = self.acquire()
        self.assertEqual(self.authority.usage('mac')['reservedBytes'], 70)
        self.assertIsNone(self.authority.acquire(PROFILES['music'], 'music', 'first'))
        self.assertTrue(self.authority.renew(lease))
    def test_expiry_does_not_free_memory(self):
        lease = self.acquire(); self.db.now = 131
        self.assertFalse(self.authority.renew(lease))
        self.assertEqual(self.authority.usage('mac')['expiredRetained'], 1)
        self.assertIsNone(self.authority.acquire(PROFILES['music'], 'music', 'first'))
    def test_unload_proof_required_even_after_expiry(self):
        lease = self.acquire(); self.db.now = 200
        with self.assertRaisesRegex(ValueError, 'UNLOAD_NOT_VERIFIED'):
            self.authority.release(lease)
        self.assertEqual(self.authority.usage('mac')['reservedBytes'], 70)
    def test_release_idempotent_and_allows_new_memory(self):
        lease = self.acquire(); self.unloaded = True
        self.authority.release(lease); self.authority.release(lease)
        self.assertEqual(self.authority.usage('mac')['reservedBytes'], 0)
        self.assertIsNotNone(self.authority.acquire(PROFILES['music'], 'music', 'first'))
        self.assertFalse(self.authority.renew(lease))
    def test_replay_same_grant_and_fresh_key_cannot_duplicate(self):
        lease = self.acquire()
        self.assertEqual(lease, self.acquire())
        with self.assertRaisesRegex(ValueError, 'RECOVERY_REQUIRED'): self.acquire(key='other')
        self.assertEqual(self.authority.usage('mac')['reservations'], 1)
    def test_tampered_memory_host_and_token_rejected(self):
        lease = self.acquire(); self.unloaded = True
        for bad in (replace(lease, memory_bytes=1), replace(lease, host_id='other'), replace(lease, token='other')):
            self.assertFalse(self.authority.renew(bad))
            with self.assertRaisesRegex(ValueError, 'LEASE_MISMATCH'): self.authority.release(bad)
    def test_server_profile_is_canonical(self):
        with self.assertRaisesRegex(ValueError, 'PROFILE_INVALID'):
            self.authority.acquire(replace(PROFILES['animation'], resource='cpu-image'), 'video', 'first')
    def test_parallel_acquire_never_oversubscribes(self):
        results = []
        barrier = threading.Barrier(2)
        def run(role):
            barrier.wait()
            results.append(self.authority.acquire(PROFILES[role], role, 'first'))
        threads = [threading.Thread(target=run, args=(role,)) for role in ('animation', 'music')]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertLessEqual(self.authority.usage('mac')['reservedBytes'], 100)
    def test_command_key_does_not_change_role(self):
        self.acquire()
        with self.assertRaisesRegex(ValueError, 'COMMAND_CONFLICT'):
            self.authority.acquire(PROFILES['music'], 'video', 'first')


class ResidencyHTTPTests(unittest.TestCase):
    setUp = ResidencyTests.setUp
    def start_http(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.authority,
            {'test-token': ('video', 'animation'), 'other-token': ('other', 'animation')}))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def finish(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(finish)
        url = 'http://127.0.0.1:' + str(server.server_port)
        path = Path(temp.name) / 'model-journal.json'
        return url, path, HttpModelResidency(url, 'video', 'test-token', journal_path=path, timeout=2)
    def test_full_frame_http_can_reserve_explicit_alpha_budget(self):
        self.authority.models['postprocess']={'host_id':'mac','model_id':'alpha','memory_bytes':70}
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.authority,{'alpha-token':('frames','postprocess')}))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=HttpModelResidency('http://127.0.0.1:'+str(server.server_port),'frames','alpha-token',journal_path=Path(temp.name)/'journal')
            lease=client.acquire(PROFILES['postprocess'],'frames')
            self.assertEqual(lease.model_id,'alpha');self.assertEqual(self.authority.usage('mac')['reservedBytes'],70)
            self.unloaded=True;client.release(lease)
        finally:server.shutdown();server.server_close();thread.join()
    def test_http_restart_recovers_same_reservation(self):
        url, path, client = self.start_http()
        lease = client.acquire(PROFILES['animation'], 'video')
        restarted = HttpModelResidency(url, 'video', 'test-token', journal_path=path, timeout=2)
        self.assertEqual(restarted.acquire(PROFILES['animation'], 'video'), lease)
        self.assertTrue(restarted.renew(lease))
        self.unloaded = True; restarted.release(lease)
        self.assertEqual(self.authority.usage('mac')['reservedBytes'], 0)
    def test_http_other_worker_cannot_renew_or_release(self):
        url, path, client = self.start_http()
        lease = client.acquire(PROFILES['animation'], 'video')
        other = HttpModelResidency(url, 'other', 'other-token', journal_path=path.with_name('other.json'), timeout=2)
        with self.assertRaisesRegex(RuntimeError, 'HTTP_409'): other.renew(lease)
        self.unloaded = True
        with self.assertRaisesRegex(RuntimeError, 'HTTP_409'): other.release(lease)
        self.assertEqual(self.authority.usage('mac')['reservedBytes'], 70)
    def test_lost_acquire_response_replays_durable_key(self):
        _, _, client = self.start_http()
        original = client._call
        first = True
        def drop(operation, body):
            nonlocal first
            result = original(operation, body)
            if operation == 'acquire' and first:
                first = False
                raise ConnectionResetError('fixture response lost after commit')
            return result
        client._call = drop
        self.assertIsNotNone(client.acquire(PROFILES['animation'], 'video'))
        self.assertEqual(self.authority.usage('mac')['reservations'], 1)


if __name__ == '__main__': unittest.main()
