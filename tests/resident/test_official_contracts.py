"""Opt-in cross-repository contract tests; in-memory DB/temp Filex only.

NGS_RUNTIME_SOURCE and PDS_RELEASE_SOURCE identify read-only source packages.
No operating DB, port 8030, private token or active service is used.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest

from workers.resident.adapters.common_authority import CommonAuthorityBridge
from workers.resident.adapters.pds_category_files import CategoryFileClient,encode
from workers.resident.contracts import PROFILES,Receipt

NGS=os.environ.get('NGS_RUNTIME_SOURCE')
PDS=os.environ.get('PDS_RELEASE_SOURCE')

@unittest.skipUnless(NGS,'NGS_RUNTIME_SOURCE not selected')
class OfficialCommon(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0,NGS)
        from local.services.request_pipeline.common import CommonRequests
        self.db=sqlite3.connect(':memory:',isolation_level=None,check_same_thread=False)
        self.addCleanup(self.db.close)
        self.now=100
        self.c=CommonRequests(db=self.db,clock=lambda:self.now)
        self.items=[]
        def choose(p):
            for i in self.items:
                if i['capability'] in p.capabilities and self.c.row(i['requestId'])['state']=='queued':return i
        self.bridge=CommonAuthorityBridge(self.c,threading.RLock(),choose,
            lambda c,r:{'sourceReceipt':{'requestId':c.request_id,'revision':2},
                        'publication':{'verified':True,'requestId':c.request_id}},
            {'video-global':1,'cpu-image':1},verify_stopped=lambda c:True,lease_seconds=30)
        self.bridge.initialize_schema()
    def accept(self,rid,role='animation'):
        typ='npc.animation.generate' if role=='animation' else 'npc.animation.frames.generate'
        params={'fixture':rid}
        h=hashlib.sha256(json.dumps(params,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.c.accept({'requestId':rid,'projectId':'binari','requestType':typ,
           'schemaVersion':'fixture.v1','source':'fixture','requester':'fixture.actor',
           'idempotencyKey':rid,'payloadRef':{'domain':'fixture','schemaVersion':'fixture.v1',
                                           'referenceId':rid,'inputHash':h}})
        self.items.append({'requestId':rid,'requestType':typ,'inputHash':h,
            'capability':'animation.generate' if role=='animation' else 'frames.extract','inputRefs':['pds:fixture'],
            'parameters':params,'expectedRevision':'1'})
    def test_worker_guard_http_is_bound_to_claim_owner(self):
        import tempfile
        from pathlib import Path
        from workers.resident.adapters.request_api import handler
        from workers.resident.adapters.request_http import HttpRequests
        from http.server import ThreadingHTTPServer
        self.accept('test.guard.http');self.items[-1]['binding']={'projectId':'binari','targetId':'character.test','expectedRevision':'1'}
        claim=self.bridge.claim(PROFILES['animation'],'worker.1')
        guard={'npcId':'character.test','generationId':'g'*48,'requestId':'parent.original','leaseToken':'t'*64}
        self.bridge.guard_resolver=lambda c:{'guard':guard,'scopeRequestId':'parent.original','stageRequestId':c.request_id}
        http=ThreadingHTTPServer(('127.0.0.1',0),handler(self.bridge,{'a'*32:('worker.1','animation'),'b'*32:('worker.2','animation')}))
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                client=HttpRequests('http://127.0.0.1:'+str(http.server_port),'worker.1','a'*32,journal_path=Path(temp)/'one')
                self.assertEqual(client.ownership_guard(claim),guard)
                other=HttpRequests(client.base,'worker.2','b'*32,journal_path=Path(temp)/'two')
                with self.assertRaises(RuntimeError):other.ownership_guard(claim)
        finally:http.shutdown();http.server_close();thread.join()

    def test_pinned_ownership_guard_never_creates_admission(self):
        import tempfile
        from pathlib import Path
        from local.services.npc_ownership import Session
        from workers.resident.adapters.ownership_guard import PinnedOwnershipGuardResolver
        self.accept('test.owned');self.items[-1]['binding']={'projectId':'binari','targetId':'character.test','expectedRevision':'1'}
        c=self.bridge.claim(PROFILES['animation'],'worker.1')
        with tempfile.TemporaryDirectory() as temp:
            session=Session(Path(temp),'character.test','parent.original',call=lambda *a:(_ for _ in ()).throw(AssertionError('no admit')))
            self.bridge.guard_resolver=PinnedOwnershipGuardResolver(lambda claim:session)
            with self.assertRaisesRegex(ValueError,'INTAKE_PIN'):self.bridge.ownership_guard(c)
            guard={'npcId':'character.test','requestId':'parent.original','generationId':'g'*48,'leaseToken':'t'*64}
            session.save({**{k:v for k,v in guard.items() if k!='leaseToken'},'guard':guard})
            self.assertEqual(self.bridge.ownership_guard(c),guard)
            self.now=131
            with self.assertRaisesRegex(ValueError,'STALE_LEASE'):self.bridge.ownership_guard(c)
            self.now=100;session.save({'npcId':'character.test','requestId':'parent.original','generationId':'g'*48,'guard':guard,'released':True})
            with self.assertRaisesRegex(ValueError,'ORIGINAL_PIN'):self.bridge.ownership_guard(c)

    def media_receipt(self,scope):
        fields=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash','publicationHash')
        receipt={**{k:scope[k] for k in fields},'verified':True,'authority':'filex','status':'committed'}
        receipt['receiptHash']=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        return receipt
    def test_publication_reservation_survives_expiry_and_blocks_release(self):
        self.accept('test.reservation');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        reserved=self.bridge.prepare_publication(c,'a'*64)
        self.assertTrue(reserved['reserved']);self.now=131
        self.assertEqual(self.bridge.prepare_publication(c,'a'*64),reserved)
        with self.assertRaisesRegex(ValueError,'UNRESOLVED'):self.bridge.release_resource(c)
        with self.assertRaisesRegex(ValueError,'UNRESOLVED'):self.bridge.fail(c,'FAIL',False)
        with self.assertRaisesRegex(ValueError,'CONFLICT'):self.bridge.prepare_publication(c,'b'*64)
        self.bridge.finish_publication(c,'a'*64,self.media_receipt(reserved))
        receipt=Receipt(c.request_id,c.attempt_id,c.fencing_token,('asset',),'2',True)
        old_verify=self.bridge.verify_publication
        with self.assertRaisesRegex(ValueError,'RESERVED_RECEIPT'):self.bridge.complete(c,receipt,True)
        self.bridge.verify_publication=lambda c,r:{**old_verify(c,r),'reservedReceipt':self.media_receipt(reserved)}
        self.bridge.complete(c,receipt,True)
        self.assertEqual(self.c.row(c.request_id)['state'],'completed');self.bridge.release_resource(c)
    def test_live_publication_authority_rejects_expired_changed_released(self):
        from dataclasses import replace
        self.accept('test.authorization');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.assertTrue(self.bridge.authorize_publication(c)['authorized'])
        with self.assertRaisesRegex(ValueError,'ISSUED_CLAIM_CHANGED'):
            self.bridge.authorize_publication(replace(c,expected_revision='999'))
        self.now=131
        with self.assertRaisesRegex(ValueError,'STALE_LEASE'):self.bridge.authorize_publication(c)
        self.now=100;self.bridge.release_resource(c)
        with self.assertRaisesRegex(ValueError,'ALREADY_RELEASED'):self.bridge.authorize_publication(c)
    def test_service_only_publication_authority_http(self):
        from workers.resident.adapters.publication_authority import handler,PublicationAuthorityClient
        from http.server import ThreadingHTTPServer
        import urllib.error
        self.accept('test.authorization');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        http=ThreadingHTTPServer(('127.0.0.1',0),handler(self.bridge,['s'*32]))
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(http.server_port)
            client=PublicationAuthorityClient(base,'s'*32)
            self.assertTrue(client(c)['authorized'])
            reservation=client.prepare(c,'c'*64)
            self.assertEqual(reservation['projectId'],'binari')
            self.assertEqual(reservation['targetId'],c.request_id)
            self.assertTrue(client.finish(c,'c'*64,self.media_receipt(reservation))['committed'])
            with self.assertRaises(urllib.error.HTTPError):PublicationAuthorityClient(base,'worker-token')(c)
            self.now=131
            with self.assertRaises(urllib.error.HTTPError):client(c)
        finally:http.shutdown();http.server_close();thread.join()
    def test_formal_frame_accept_and_bridge_preserve_payload(self):
        import tempfile
        from local.services.frame_requests.service import FrameRequests
        from local.services.request_pipeline.common import digest
        with tempfile.TemporaryDirectory() as root:
            service=FrameRequests(self.c,root,None)
            request={'schemaVersion':'ngs.npc-animation-frames-request.v1','projectId':'binari',
                     'npcId':'character.fixture','posePreset':'idle','presetId':'walk',
                     'intent':'정식 프레임 생성','scalePreset':'full'}
            accepted=service.accept('fixture.actor','formal.frame',request)
            self.assertEqual(service.accept('fixture.actor','formal.frame',request)['requestId'],accepted['requestId'])
            self.items.append({'requestId':accepted['requestId'],'requestType':'npc.animation.frames.generate',
                'inputHash':digest(request),'capability':'frames.extract','inputRefs':['pds:fixture'],
                'parameters':request,'expectedRevision':'1'})
            c=self.bridge.claim(PROFILES['postprocess'],'worker.frame')
            self.assertEqual(c.parameters,request)
            self.assertEqual(c.parameters['scalePreset'],'full')
    def test_concurrent_workers_share_one_video_grant(self):
        self.accept('test.1');self.accept('test.2')
        barrier=threading.Barrier(2);results=[]
        def pull(name):
            barrier.wait();results.append(self.bridge.claim(PROFILES['animation'],name))
        threads=[threading.Thread(target=pull,args=(f'worker.{i}',)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(c is not None for c in results),1)
    def test_payload_mutation_after_formal_accept_is_denied(self):
        self.accept('test.1');self.items[0]['parameters']['fixture']='tampered'
        with self.assertRaisesRegex(ValueError,'INPUT_HASH'):
            self.bridge.claim(PROFILES['animation'],'worker.1')
        self.assertEqual(self.c.row('test.1')['state'],'queued')
    def test_real_common_claim_heartbeat_complete(self):
        self.accept('test.1');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.assertEqual(self.c.row(c.request_id)['state'],'processing')
        self.now=110;self.assertTrue(self.bridge.renew(c))
        receipt=Receipt(c.request_id,c.attempt_id,c.fencing_token,('asset',),'2',True)
        self.bridge.complete(c,receipt,True)
        self.assertEqual(self.c.row(c.request_id)['state'],'completed')
        self.bridge.release_resource(c)
    def test_video_capacity_one_but_frames_can_claim(self):
        self.accept('test.1');self.accept('test.2');self.accept('test.3','postprocess')
        c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.assertIsNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
        frame=self.bridge.claim(PROFILES['postprocess'],'worker.frame')
        self.assertIsNotNone(frame)
        self.bridge.release_resource(c)
        self.assertIsNotNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
    def test_expired_lease_does_not_free_live_video_slot(self):
        self.accept('test.1');self.accept('test.2')
        c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.now=200
        self.assertFalse(self.bridge.renew(c))
        self.assertIsNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
    def test_cleanup_pending_is_not_completed_or_regenerated(self):
        self.accept('test.1');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.bridge.complete(c,Receipt(c.request_id,c.attempt_id,c.fencing_token,('a',),'2',True),False)
        self.assertEqual(self.c.row(c.request_id)['state'],'sync_pending')
        self.bridge.release_resource(c)
        self.assertIsNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
    def test_bad_input_hash_rolls_back_claim_and_resource(self):
        self.accept('test.1');self.items[0]['inputHash']='0'*64
        with self.assertRaisesRegex(ValueError,'INPUT_HASH'):self.bridge.claim(PROFILES['animation'],'worker.1')
        self.assertEqual(self.c.row('test.1')['state'],'queued')
        self.assertEqual(self.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],0)
    def test_noncanonical_profile_cannot_bypass_video_resource(self):
        from dataclasses import replace
        self.accept('test.1')
        profile=replace(PROFILES['animation'],resource='cpu-image')
        with self.assertRaisesRegex(ValueError,'NONCANONICAL'):
            self.bridge.claim(profile,'worker.1')
        self.assertEqual(self.c.row('test.1')['state'],'queued')
    def test_expired_after_backend_exit_is_blocked_before_slot_release(self):
        import tempfile
        from test_runtime import FakePDS,FakeBackend
        from workers.resident.runtime import ResidentWorker
        self.accept('test.1');self.accept('test.2')
        backend=FakeBackend()
        backend.hook=lambda cancel:setattr(self,'now',200)
        with tempfile.TemporaryDirectory() as root:
            worker=ResidentWorker(PROFILES['animation'],'worker.1',self.bridge,
                                   FakePDS(Path(root)),backend,heartbeat_seconds=.01)
            worker.poll_once()
        self.assertEqual(self.c.row('test.1')['state'],'blocked')
        self.assertEqual(self.c.row('test.1')['error'],'LEASE_LOST')
        self.assertIsNotNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
    def test_wrong_request_publication_proof_is_denied(self):
        self.accept('test.1');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.bridge.verify_publication=lambda *a:{'sourceReceipt':{'requestId':'other'},
            'publication':{'requestId':'other','verified':True}}
        with self.assertRaisesRegex(ValueError,'SERVER_PUBLICATION'):
            self.bridge.complete(c,Receipt(c.request_id,c.attempt_id,c.fencing_token,('a',),'2',True),True)
        self.assertEqual(self.c.row(c.request_id)['state'],'processing')
    def test_expired_execution_cannot_publish_success(self):
        self.accept('test.1');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        self.now=200
        with self.assertRaisesRegex(ValueError,'STALE_LEASE'):
            self.bridge.complete(c,Receipt(c.request_id,c.attempt_id,c.fencing_token,('a',),'2',True),True)
        self.assertEqual(self.c.row(c.request_id)['state'],'processing')
        self.assertEqual(self.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],1)
    def test_without_trusted_stop_proof_grant_cannot_be_released(self):
        self.accept('test.1');self.accept('test.2')
        c=self.bridge.claim(PROFILES['animation'],'worker.1');self.now=200
        self.bridge.verify_stopped=lambda c:False
        with self.assertRaisesRegex(ValueError,'STOP_NOT_VERIFIED'):
            self.bridge.fail(c,'LEASE_LOST',False)
        with self.assertRaisesRegex(ValueError,'STOP_NOT_VERIFIED'):
            self.bridge.release_resource(c)
        self.assertIsNone(self.bridge.claim(PROFILES['animation'],'worker.2'))
    def test_stale_fence_cannot_complete(self):
        from dataclasses import replace
        self.accept('test.1');c=self.bridge.claim(PROFILES['animation'],'worker.1')
        bad=replace(c,fencing_token='999')
        with self.assertRaisesRegex(ValueError,'ISSUED_CLAIM_CHANGED'):
            self.bridge.complete(bad,Receipt(bad.request_id,bad.attempt_id,'999',('a',),'2',True),True)

@unittest.skipUnless(PDS,'PDS_RELEASE_SOURCE not selected')
class OfficialPDS(unittest.TestCase):
    def setUp(self):
        sys.path[:0]=[str(Path(PDS)/'data-service'),str(Path(PDS)/'data-service/tests')]
        from test_npc_category_files import Files
        from project_data_service.http_server import Handler
        from http.server import ThreadingHTTPServer
        from types import SimpleNamespace
        self.fixture=Files('test_replace_only_target_and_receipt_recovery')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http.app=SimpleNamespace(domains=self.fixture.domains)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        def finish():self.http.shutdown();self.http.server_close();self.thread.join()
        self.addCleanup(finish)
        self.client=CategoryFileClient('http://127.0.0.1:'+str(self.http.server_port),
                                      lambda:self.fixture.guard,'planning.stage',timeout=2)
    def test_download_edit_raw_file_replace_actual_pds_filex(self):
        raw,basis=self.client.download('character.test','art',1)
        body=json.loads(raw);body['content']='새 워커 파일 전송 격리 검증'
        meta={'projectId':'ProjectBS','expectedRevision':1,'npcId':'character.test',
              'categoryId':'art','mode':'modify','operationHash':'e'*64,
              'metadata':{'inputHash':'d'*64,'categoryId':'art'},'basis':basis,'note':'fixture'}
        receipt=self.client.replace(encode(body),meta)
        self.assertEqual(receipt['revision'],2)
        current=self.fixture.domains.npcs.current()['document']['characters'][0]
        self.assertEqual(current['planningCategories'][0]['content'],body['content'])
        self.assertEqual(current['planningCategories'][0]['unknown'],'preserve')
        self.assertNotIn('verified',receipt) # write receipt is not publication completion!
        self.assertEqual(self.client.replace(encode(body),meta),receipt)
    def test_wrong_stage_or_npc_rejected(self):
        with self.assertRaisesRegex(ValueError,'GUARD_MISMATCH'):
            self.client.download('character.other','art',1)
        raw,basis=self.client.download('character.test','art',1)
        basis['stageRequestId']='wrong.stage'
        with self.assertRaisesRegex(ValueError,'STAGE_MISMATCH'):
            self.client.replace(raw,{'projectId':'ProjectBS','npcId':'character.test','basis':basis})
    def test_scope_change_rejected_by_actual_pds(self):
        raw,basis=self.client.download('character.test','art',1)
        body=json.loads(raw);body['id']='other'
        meta={'projectId':'ProjectBS','expectedRevision':1,'npcId':'character.test',
              'categoryId':'art','mode':'modify','operationHash':'e'*64,
              'metadata':{'inputHash':'d'*64,'categoryId':'art'},'basis':basis,'note':'fixture'}
        with self.assertRaisesRegex(ValueError,'CATEGORY_FILE_SCOPE_CHANGED'):
            self.client.replace(encode(body),meta)

@unittest.skipUnless(NGS,'NGS_RUNTIME_SOURCE not selected')
class OfficialHTTP(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        from workers.resident.adapters.request_api import handler
        from workers.resident.adapters.request_http import HttpRequests
        import tempfile
        self.journal_root=tempfile.TemporaryDirectory();self.addCleanup(self.journal_root.cleanup)
        self.journal=Path(self.journal_root.name)/'worker1.json'
        self.base=OfficialCommon('test_real_common_claim_heartbeat_complete')
        self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.base.accept('http.1')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),handler(self.base.bridge,
            {'fixture-token-one':('worker.1','animation'),
             'fixture-token-two':('worker.2','animation')}))
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        def finish():self.http.shutdown();self.http.server_close();self.thread.join()
        self.addCleanup(finish)
        self.url='http://127.0.0.1:'+str(self.http.server_port)
        self.client=HttpRequests(self.url,'worker.1','fixture-token-one',journal_path=self.journal,timeout=2)
    def test_runtime_uses_http_not_database(self):
        import tempfile
        from test_runtime import FakePDS,FakeBackend
        from workers.resident.runtime import ResidentWorker
        with tempfile.TemporaryDirectory() as root:
            worker=ResidentWorker(PROFILES['animation'],'worker.1',self.client,
                                   FakePDS(Path(root)),FakeBackend(),heartbeat_seconds=.01)
            self.assertTrue(worker.poll_once())
        self.assertEqual(self.base.c.row('http.1')['state'],'completed')
        self.assertEqual(self.base.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],0)
    def test_other_worker_cannot_renew_or_release_grant(self):
        from workers.resident.adapters.request_http import HttpRequests
        c=self.client.claim(PROFILES['animation'],'worker.1')
        other=HttpRequests(self.url,'worker.2','fixture-token-two',journal_path=Path(self.journal_root.name)/'worker2.json')
        with self.assertRaisesRegex(RuntimeError,'409'):other.renew(c)
        with self.assertRaisesRegex(RuntimeError,'409'):other.release_resource(c)
        self.assertEqual(self.base.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],1)
    def test_invalid_token_cannot_claim(self):
        from workers.resident.adapters.request_http import HttpRequests
        bad=HttpRequests(self.url,'worker.1','not-valid',journal_path=self.journal)
        with self.assertRaisesRegex(RuntimeError,'401'):bad.claim(PROFILES['animation'],'worker.1')
        self.assertEqual(self.base.c.row('http.1')['state'],'queued')
    def test_claim_response_drop_replays_same_committed_claim(self):
        import socket
        original=self.http.RequestHandlerClass.reply
        dropped=[]
        def reply(handler,status,value):
            if status==200 and value.get('claim') and not dropped:
                dropped.append(True);handler.close_connection=True
                handler.connection.shutdown(socket.SHUT_RDWR);handler.connection.close();return
            return original(handler,status,value)
        self.http.RequestHandlerClass.reply=reply
        c=self.client.claim(PROFILES['animation'],'worker.1')
        self.assertEqual(c.fencing_token,'1')
        self.assertEqual(len(dropped),1)
        self.assertEqual(self.base.db.execute('select count(*) from resident_claim_commands').fetchone()[0],1)
        self.assertEqual(self.base.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],1)
    def test_claim_key_survives_client_restart(self):
        from workers.resident.adapters.request_http import HttpRequests
        c=self.client.claim(PROFILES['animation'],'worker.1')
        restarted=HttpRequests(self.url,'worker.1','fixture-token-one',journal_path=self.journal)
        self.assertEqual(restarted.claim(PROFILES['animation'],'worker.1'),c)
    def test_issued_input_contract_cannot_be_changed(self):
        from dataclasses import replace
        c=self.client.claim(PROFILES['animation'],'worker.1')
        for bad in (replace(c,expected_revision='999'),replace(c,input_refs=('other',)),
                    replace(c,parameters={'tampered':True})):
            with self.assertRaisesRegex(RuntimeError,'409'):self.client.renew(bad)
            with self.assertRaisesRegex(RuntimeError,'409'):self.client.fail(bad,'NO_ARTIFACTS',False)
            with self.assertRaisesRegex(RuntimeError,'409'):self.client.release_resource(bad)
        self.assertEqual(self.base.c.row('http.1')['state'],'processing')
        self.assertEqual(self.base.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],1)
    def test_release_is_idempotent_and_old_claim_is_not_reexecuted(self):
        c=self.client.claim(PROFILES['animation'],'worker.1')
        self.client.fail(c,'NO_ARTIFACTS',True)
        self.client.release_resource(c)
        self.client.release_resource(c)
        self.assertEqual(self.base.db.execute('select count(*) from resident_resource_grants where released=0').fetchone()[0],0)
    def test_request_api_has_bounded_timeout(self):
        from workers.resident.adapters.request_http import HttpRequests
        with self.assertRaisesRegex(ValueError,'CONFIG_INVALID'):
            HttpRequests(self.url,'worker.1','fixture-token-one',journal_path=self.journal,timeout=60)
