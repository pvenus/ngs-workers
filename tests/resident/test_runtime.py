import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

from workers.resident.contracts import Claim, PROFILES, Receipt
from workers.resident.runtime import ResidentWorker, ReconciliationRequired


class FakeRequests:
    def __init__(self, claims):
        self.claims, self.completed, self.failed, self.unknown = list(claims), [], [], []
        self.released, self.valid, self.lose_ack = [], True, False
    def claim(self, profile, worker_id):
        return self.claims.pop(0) if self.claims else None
    def renew(self, claim): return self.valid
    def checkpoint(self,claim,receipt):receipt.validate(claim)
    def complete(self, claim, receipt, cleanup_done):
        self.completed.append((claim, receipt, cleanup_done))
        if self.lose_ack: raise ConnectionError('response lost after commit')
    def fail(self, claim, code, cleanup_done): self.failed.append((claim, code, cleanup_done))
    def uncertain(self, claim, code): self.unknown.append((claim, code))
    def execution_unknown(self,claim,code):self.unknown.append((claim,code))
    def release_resource(self, claim): self.released.append(claim)


class FakePDS:
    def __init__(self, root):
        self.root, self.published, self.cleaned = root, [], []
        self.corrupt = self.lose_ack = self.cleanup_fails = False
    def stage(self, claim):
        p = self.root / claim.attempt_id
        p.mkdir()
        return p
    def publish(self, claim, workspace, artifacts):
        self.published.append(claim)
        if self.lose_ack: raise ConnectionError('publish response lost')
        return Receipt(claim.request_id, claim.attempt_id,
                       'wrong' if self.corrupt else claim.fencing_token,
                       ('filex:asset',), 'rev2', True)
    def cleanup(self, claim, workspace):
        self.cleaned.append(claim)
        return not self.cleanup_fails


class FakeBackend:
    def __init__(self):
        self.loads = self.unloads = self.executions = 0
        self.hook = None
    def load(self): self.loads += 1
    def unload(self): self.unloads += 1
    def execute(self, claim, workspace, cancel):
        self.executions += 1
        if self.hook: self.hook(cancel)
        p = workspace / 'output.bin'
        p.write_bytes(b'isolated fixture')
        return (p,)


def claim(role='animation', n=1):
    return Claim(f'r{n}', f'a{n}', f'f{n}', PROFILES[role].capabilities[0],
                 ('pds:input',), {}, 'resource-grant', 'source-rev1')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = FakeRequests([claim()])
        self.p = FakePDS(Path(self.tmp.name))
        self.b = FakeBackend()
        self.now = 0
        self.w = ResidentWorker(PROFILES['animation'], 'worker-1', self.r, self.p, self.b,
                                idle_seconds=10, heartbeat_seconds=.01, clock=lambda:self.now)
    def test_profiles_are_five_and_video_resource_is_global(self):
        self.assertEqual(set(PROFILES), {'document','image','animation','postprocess','music'})
        self.assertEqual(PROFILES['animation'].resource,'video-global')
        self.assertEqual(PROFILES['animation'].capacity,1)
    def test_lazy_load_and_idle_unload(self):
        self.assertEqual(self.b.loads,0)
        self.w.poll_once()
        self.assertEqual(self.b.loads,1)
        self.now = 9; self.w.poll_once()
        self.assertEqual(self.b.unloads,0)
        self.now = 10; self.w.poll_once()
        self.assertEqual(self.b.unloads,1)
    def test_two_requests_reuse_model(self):
        self.r.claims.append(claim(n=2))
        self.w.poll_once(); self.w.poll_once()
        self.assertEqual((self.b.loads,self.b.executions),(1,2))
    def test_checkpoint_failure_preserves_scratch_before_cleanup(self):
        def fail(*args):raise ConnectionError('fixture checkpoint response lost')
        self.r.checkpoint=fail
        with self.assertRaises(ReconciliationRequired):self.w.poll_once()
        self.assertEqual(len(self.p.cleaned),0);self.assertEqual(len(self.r.completed),0)
    def test_success_requires_pds_receipt_and_cleanup(self):
        self.w.poll_once()
        self.assertEqual(len(self.r.completed),1)
        self.assertTrue(self.r.completed[0][2])
        self.assertEqual(len(self.p.published),1)
        self.assertEqual(len(self.p.cleaned),1)
        self.assertEqual(len(self.r.released),1)
    def test_cleanup_pending_does_not_regenerate(self):
        self.p.cleanup_fails=True
        self.w.poll_once()
        self.assertFalse(self.r.completed[0][2])
        self.assertEqual(self.b.executions,1)
    def test_cleanup_exception_is_pending_not_publication_unknown(self):
        def broken_cleanup(*args): raise OSError('temporary cleanup failed')
        self.p.cleanup=broken_cleanup
        self.w.poll_once()
        self.assertEqual(len(self.r.completed),1)
        self.assertFalse(self.r.completed[0][2])
        self.assertFalse(self.r.unknown)
        self.assertFalse(self.w.stop.is_set())
    def test_partial_stage_failure_uses_attempt_cleanup(self):
        seen=[]
        def stage(c):
            (self.p.root / 'partial').write_bytes(b'partial download')
            raise OSError('stage interrupted')
        def cleanup(c, workspace):
            seen.append(workspace)
            return False
        self.p.stage=stage; self.p.cleanup=cleanup
        self.w.poll_once()
        self.assertEqual(seen,[None])
        self.assertFalse(self.r.failed[0][2])
        self.assertFalse(self.p.published)
    def test_wrong_capability_never_loads_or_stages(self):
        self.r.claims=[claim('music')]
        self.w.poll_once()
        self.assertEqual(self.b.loads,0)
        self.assertEqual(len(self.r.failed),1)
        self.assertFalse(self.p.published)
    def test_lost_lease_cancels_and_never_publishes(self):
        def lose(cancel):
            self.r.valid=False
            self.assertTrue(cancel.wait(1))
        self.b.hook=lose
        self.w.poll_once()
        self.assertFalse(self.p.published)
        self.assertEqual(len(self.r.failed),1)
    def test_heartbeat_keeps_running_during_generation(self):
        count=[]
        def renew(c): count.append(c); return True
        self.r.renew=renew
        self.b.hook=lambda cancel: time.sleep(.05)
        self.w.poll_once()
        self.assertGreaterEqual(len(count),3)
    def test_receipt_mismatch_blocks_and_preserves_evidence(self):
        self.p.corrupt=True
        with self.assertRaises(ReconciliationRequired): self.w.poll_once()
        self.assertFalse(self.r.completed)
        self.assertFalse(self.p.cleaned)
        self.assertTrue(self.w.stop.is_set())
    def test_publish_unknown_never_retries_or_cleans(self):
        self.p.lose_ack=True
        with self.assertRaises(ReconciliationRequired): self.w.poll_once()
        self.assertFalse(self.w.poll_once())
        self.assertEqual(self.b.executions,1)
        self.assertFalse(self.p.cleaned)
        self.assertEqual(len(self.r.unknown),1)
    def test_completion_ack_unknown_never_regenerates(self):
        self.r.lose_ack=True
        with self.assertRaises(ReconciliationRequired): self.w.poll_once()
        self.assertEqual(len(self.r.completed),1)
        self.assertFalse(self.w.poll_once())
    def test_backend_failure_cleans_and_releases(self):
        def boom(c): raise RuntimeError('fixture')
        self.b.hook=boom
        self.w.poll_once()
        self.assertEqual(len(self.p.cleaned),1)
        self.assertEqual(len(self.r.failed),1)
        self.assertEqual(len(self.r.released),1)
    def test_source_output_outside_workspace_is_rejected(self):
        outside=Path(self.tmp.name)/'outside'; outside.write_bytes(b'x')
        self.b.execute=lambda *a:(outside,)
        self.w.poll_once()
        self.assertFalse(self.p.published)
    def test_nonmodel_worker_does_not_load(self):
        self.r.claims=[claim('postprocess')]
        self.w.profile=PROFILES['postprocess']
        self.w.poll_once()
        self.assertEqual(self.b.loads,0)
    def test_receipt_verified_must_be_true_boolean(self):
        c=claim()
        with self.assertRaises(ValueError):
            Receipt(c.request_id,c.attempt_id,c.fencing_token,('asset',),'r2','false').validate(c)
    def test_safe_domain_error_code_is_preserved(self):
        self.b.execute=lambda *a:()
        self.w.poll_once()
        self.assertEqual(self.r.failed[0][1],'NO_ARTIFACTS')
    def test_unconfirmed_process_exit_never_cleans_or_releases(self):
        from workers.resident.contracts import ExecutionStopUnconfirmed
        def unknown(cancel):raise ExecutionStopUnconfirmed('child may be alive')
        self.b.hook=unknown
        with self.assertRaises(ExecutionStopUnconfirmed):self.w.poll_once()
        self.assertFalse(self.p.cleaned);self.assertFalse(self.r.released)
        self.assertFalse(self.r.failed);self.assertTrue(self.w.stop.is_set())
        self.assertEqual(self.r.unknown[0][1],'EXECUTION_STOP_UNCONFIRMED')
    def test_stop_prevents_new_claim(self):
        self.w.stop.set()
        self.assertFalse(self.w.poll_once())
        self.assertEqual(len(self.r.claims),1)
    def test_serve_finally_unloads(self):
        self.b.hook=lambda c:self.w.stop.set()
        self.w.serve(.001)
        self.assertEqual(self.b.unloads,1)


if __name__ == '__main__': unittest.main()
