import os,unittest,json,threading
import test_official_contracts as fixtures
from workers.resident.contracts import PROFILES,Receipt
from workers.resident.adapters.reconciliation import ReconciliationSupervisor

@unittest.skipUnless(os.environ.get('NGS_RUNTIME_SOURCE'),'explicit isolated source required')
class RecoveryTests(unittest.TestCase):
    setUp=fixtures.OfficialCommon.setUp
    accept=fixtures.OfficialCommon.accept
    media_receipt=fixtures.OfficialCommon.media_receipt
    def setup_recovery(self,*,reserved=False):
        self.accept('test.recovery');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        receipt=Receipt(c.request_id,c.attempt_id,c.fencing_token,('asset',),'2',True)
        if reserved:
            reservation=self.bridge.prepare_publication(c,'f'*64)
            self.raw=self.media_receipt(reservation)
            verify=self.bridge.verify_publication
            self.bridge.verify_publication=lambda c,r:{**verify(c,r),'reservedReceipt':self.raw}
        self.inspect_calls=0;self.cleanup_calls=0;self.stopped=True;self.clean=True;self.published=True
        self.bridge.verify_stopped=lambda c:self.stopped
        def inspect(c):self.inspect_calls+=1;return receipt if self.published else None
        def cleanup(c):self.cleanup_calls+=1;return self.clean
        self.supervisor=ReconciliationSupervisor(self.bridge,inspect,cleanup,poll_seconds=1)
        self.supervisor.initialize_schema();self.now=131
        return c,receipt
    def test_recovery_checkpoints_proof_before_destructive_cleanup_and_owner_loss(self):
        c,r=self.setup_recovery();calls=[]
        def cleanup(c):
            calls.append(c)
            self.supervisor.inspect_publication=lambda c:(_ for _ in ()).throw(AssertionError('evidence removed'))
            self.bridge.verify_publication=lambda *a:(_ for _ in ()).throw(AssertionError('evidence removed'))
            if len(calls)==1:self.now+=61
            return True
        self.supervisor.cleanup=cleanup
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'unknown')])
        self.assertIsNotNone(self.db.execute('SELECT proof_json FROM resident_publication_checkpoints').fetchone())
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
        self.assertEqual(len(calls),2)
    def test_durable_checkpoint_recovers_after_scratch_deleted(self):
        c,r=self.setup_recovery();self.now=101
        self.bridge.checkpoint(c,r);self.now=131
        self.supervisor.inspect_publication=lambda c:(_ for _ in ()).throw(AssertionError('scratch gone'))
        self.bridge.verify_publication=lambda *a:(_ for _ in ()).throw(AssertionError('use verified durable checkpoint'))
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
    def test_completed_ack_loss_only_releases_leftover_resource(self):
        c,r=self.setup_recovery();self.now=101
        self.bridge.complete(c,r,True)
        self.supervisor.inspect_publication=lambda c:(_ for _ in ()).throw(AssertionError('scratch gone'))
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')]);self.assertEqual(self.c.row(c.request_id)['fence'],1)
    def test_expired_legacy_uses_new_finalize_fence_not_new_generation(self):
        c,r=self.setup_recovery();self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
        row=self.c.row(c.request_id);self.assertEqual(row['state'],'completed');self.assertEqual(row['fence'],2)
        self.assertEqual(self.cleanup_calls,1);self.assertEqual(self.supervisor.tick(),[])
        events=self.c.view(c.request_id)['events'];self.assertTrue(any(e['details'].get('generationCalls')==0 for e in events))
    def test_unknown_execution_and_404_never_release(self):
        c,_=self.setup_recovery();self.stopped=False
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'unknown')]);self.assertEqual(self.inspect_calls,0)
        self.now+=10;self.stopped=True;self.published=False
        self.supervisor.tick();self.assertEqual(self.cleanup_calls,0)
        self.assertEqual(self.db.execute('SELECT released FROM resident_resource_grants').fetchone()[0],0)
    def test_only_reserved_same_hash_can_resume_publication(self):
        c,r=self.setup_recovery(reserved=True);self.published=False;calls=[]
        def resume(c,h):calls.append(h);return r
        self.supervisor.resume_publication=resume
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')]);self.assertEqual(calls,['f'*64])
    def test_unreserved_404_never_reposts_or_regenerates(self):
        c,r=self.setup_recovery();self.published=False
        self.supervisor.resume_publication=lambda *a:(_ for _ in ()).throw(AssertionError('unreserved repost'))
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'unknown')])
    def test_prepared_receipt_recovers_finish_then_completion(self):
        c,_=self.setup_recovery(reserved=True)
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
        self.assertEqual(self.db.execute('SELECT state FROM resident_publications').fetchone()[0],'committed')
    def test_released_legacy_unknown_is_still_reconciled(self):
        c,_=self.setup_recovery();self.now=101
        self.bridge.uncertain(c,'ACK_UNKNOWN');self.bridge.release_resource(c)
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
    def test_released_grant_with_committed_publication_is_still_reconciled(self):
        c,_=self.setup_recovery(reserved=True);self.now=101
        self.bridge.finish_publication(c,'f'*64,self.raw);self.bridge.uncertain(c,'ACK_UNKNOWN');self.bridge.release_resource(c)
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')])
    def test_cleanup_pending_retries_without_generation(self):
        c,_=self.setup_recovery();self.clean=False
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'cleanup_pending')])
        self.assertEqual(self.c.row(c.request_id)['state'],'sync_pending')
        self.now+=10;self.clean=True
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'completed')]);self.assertEqual(self.cleanup_calls,2)
    def test_old_unknown_event_does_not_preempt_current_healthy_attempt(self):
        c,_=self.setup_recovery();self.now=101
        self.c.event(c.request_id,'resident.execution-unknown.old','execution_unknown','processing','processing',{})
        self.assertEqual(self.supervisor.tick(),[]);self.assertEqual(self.inspect_calls,0)
    def test_renewed_producer_is_rechecked_after_probe(self):
        c,r=self.setup_recovery()
        def probe(c):
            self.now=101;self.bridge.renew(c)
            return r
        self.supervisor.inspect_publication=probe
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'unknown')])
        self.assertEqual(self.c.row(c.request_id)['fence'],1);self.assertEqual(self.cleanup_calls,0)
    def test_replaced_original_attempt_is_never_recovered(self):
        c,_=self.setup_recovery()
        self.c.claim(c.request_id,'other.worker','other.key',states=('processing',))
        self.assertEqual(self.supervisor.tick(),[]);self.assertEqual(self.inspect_calls,0)
    def test_lost_recovery_owner_does_not_finalize(self):
        c,r=self.setup_recovery()
        def late(c):self.now+=61;return r
        self.supervisor.inspect_publication=late
        self.assertEqual(self.supervisor.tick(),[(c.request_id,'unknown')]);self.assertEqual(self.c.row(c.request_id)['state'],'processing')
        self.assertEqual(self.cleanup_calls,0)
    def test_expired_model_kept_until_actual_unload_proof(self):
        from workers.resident.adapters.model_residency import ModelResidencyAuthority
        c,_=self.setup_recovery();self.published=False
        self.unloaded=False
        a=ModelResidencyAuthority(self.c,threading.RLock(),{'mac':100},{'animation':{'host_id':'mac','model_id':'wan','memory_bytes':70}},verify_unloaded=lambda l:self.unloaded,lease_seconds=5)
        a.initialize_schema();a.acquire(PROFILES['animation'],'worker.1','first')
        self.supervisor.model_authority=a;self.now+=6;self.supervisor.tick();self.assertEqual(a.usage('mac')['reservedBytes'],70)
        self.unloaded=True;self.supervisor.tick();self.assertEqual(a.usage('mac')['reservedBytes'],0)
