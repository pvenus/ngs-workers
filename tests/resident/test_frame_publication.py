import copy
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from workers.resident.adapters.pds_frames import FramePublicationClient

@unittest.skipUnless(os.environ.get('PDS_RELEASE_SOURCE'),'PDS_RELEASE_SOURCE not selected')
class FramePublicationContract(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ['PDS_RELEASE_SOURCE'])
        sys.path[:0]=[str(root/'data-service'),str(root/'data-service/tests')]
        from test_npc_animation_frames import Tests
        from project_data_service.http_server import Handler
        self.fixture=Tests('test_publish_roundtrip_replay_and_originals')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.packet=self.fixture.packet()
        # Same schema and fixture data, thread-safe test DB for HTTP requests.
        old=self.fixture.domain.npc_receipts.connection
        db=sqlite3.connect(':memory:',check_same_thread=False);old.backup(db)
        self.fixture.domain.npc_receipts.connection=db;self.addCleanup(db.close)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http.app=SimpleNamespace(domains=self.fixture.domain)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        def finish():self.http.shutdown();self.http.server_close();self.thread.join()
        self.addCleanup(finish)
        guard={'npcId':self.packet['npcId'],'generationId':'a'*48,'requestId':self.packet['requestId'],'leaseToken':'b'*64}
        self.client=FramePublicationClient('http://127.0.0.1:'+str(self.http.server_port),lambda:guard,
                                           self.packet['requestId'],timeout=2)
    def test_actual_frame_http_publication_readback_and_replay(self):
        before=copy.deepcopy(self.fixture.domain.npcs.current()['document'])
        result=self.client.publish(self.packet)
        self.assertTrue(result['verified']);self.assertEqual(result['frameCount'],16)
        self.assertEqual(self.client.publish(self.packet),result)
        after=self.fixture.domain.npcs.current()['document']
        self.assertEqual(after['characters'][0]['animationSlots'],before['characters'][0]['animationSlots'])
        self.assertEqual(after['characters'][1],before['characters'][1])
        self.assertIn('combat/sword-attack',after['characters'][0]['animationFrameSets'])
    def test_stale_cas_never_overwrites(self):
        before=self.fixture.s.snapshot()
        self.packet['expectedCharacterHash']='0'*64
        with self.assertRaisesRegex(ValueError,'NPC_FRAMES_SOURCE_CHANGED'):self.client.publish(self.packet)
        self.assertEqual(self.fixture.s.snapshot(),before)
    def test_existing_receipt_cannot_hide_changed_manifest(self):
        self.client.publish(self.packet)
        self.packet['trace']['changed']=True
        with self.assertRaisesRegex(ValueError,'RECEIPT_BINDING'):self.client.publish(self.packet)
