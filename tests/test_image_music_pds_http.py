"""Actual worker HTTP client -> PDS -> Filex, isolated authority fixture only."""
import json,base64,importlib.util,os,tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from workers.resident.contracts import Claim
from workers.resident.adapters.image_pds import ImageWorkspace,MediaArtifactClient
from workers.resident.adapters.music_pds import MusicWorkspace
from workers.resident.backends.image_music_workspace import digest
@unittest.skipUnless(os.environ.get('MEDIA_PDS_SOURCE'),'explicit isolated backend fixture required')
class Tests(unittest.TestCase):
 def test_actual_client_staging_filex_receipt_and_byte_readback(self):
  source=Path(os.environ['MEDIA_PDS_SOURCE'])
  spec=importlib.util.spec_from_file_location('fixture_media_pds',source/'data-service/tests/test_media_artifacts.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  fixture=m.Tests();fixture.setUp();self.addCleanup(fixture.tearDown)
  request={'prompt':'original','model':'acestep-xl-sft-lm4b'}
  claim=Claim('request.test','attempt.1','fence.1','music.generate',(),request,'grant.fixture','0')
  p=m.packet();p.update(fencingToken=claim.fencing_token,expectedRevision=claim.expected_revision,requestHash=digest(request),claim=json.loads(json.dumps(asdict(claim))));fixture.authority.p=p
  with tempfile.TemporaryDirectory()as temp:
   client=MediaArtifactClient('http://127.0.0.1:'+str(fixture.server.server_port),m.TOKEN)
   source=lambda c,w:dict(revision='0',projectId='binari',targetId='music.example',execution={},inputFiles=[])
   port=MusicWorkspace(Path(temp).resolve(),client,source,lambda *a:True);folder=port.stage(claim)
   (folder/'music.wav').write_bytes(base64.b64decode(p['artifact']['base64']));(folder/'music-result.json').write_text('{}')
   original=client.publish;calls=[]
   def lost_response(packet):
    calls.append(packet);original(packet);raise TimeoutError('lost committed response')
   client.publish=lost_response
   with self.assertRaises(TimeoutError):port.publish(claim,folder,(folder/'music.wav',folder/'music-result.json'))
   receipt=port.recover(claim);receipt.validate(claim);self.assertEqual(len(calls),1)
   client.publish=original
   intent=json.loads((folder/'publication-intent.json').read_text())
   with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):port.resume_publication(claim,'0'*64)
   self.assertEqual(port.resume_publication(claim,digest(intent)),receipt)
   self.assertEqual(receipt.asset_refs,(fixture.authority.receipt['artifact']['assetId'],))
   self.assertEqual(port.publish(claim,folder,(folder/'music.wav',folder/'music-result.json')),receipt)
 @unittest.skipUnless(os.environ.get('NGS_RUNTIME_SOURCE'),'explicit Common source required')
 def test_new_music_intake_real_authority_http_pds_filex(self):
  import sys,sqlite3,threading,urllib.request
  from unittest.mock import patch
  from http.server import ThreadingHTTPServer
  from workers.resident.adapters.music_intake import MusicIntake,TYPE
  from workers.resident.adapters.common_authority import CommonAuthorityBridge
  from workers.resident.adapters.publication_authority import handler
  from workers.resident.contracts import PROFILES
  sys.path.insert(0,os.environ['NGS_RUNTIME_SOURCE']);self.addCleanup(lambda:sys.path.remove(os.environ['NGS_RUNTIME_SOURCE']))
  from local.services.request_pipeline import common
  db=sqlite3.connect(':memory:',isolation_level=None,check_same_thread=False);self.addCleanup(db.close)
  c=common.CommonRequests(db=db);intake=MusicIntake(c);intake.initialize_schema()
  request={'prompt':'official fixture music','model':'acestep-xl-sft-lm4b','kind':'music','duration_seconds':10,'seed':42}
  with patch.object(common,'KINDS',common.KINDS|{TYPE}):intake.accept('actor','key','binari','music.example','0',request)
  rid=db.execute('select request_id from resident_music_payloads').fetchone()[0]
  bridge=CommonAuthorityBridge(c,threading.RLock(),lambda p:intake.payload(rid),lambda *a:None,{'video-global':1,'music-model':1},verify_stopped=lambda c:True)
  bridge.initialize_schema();claim=bridge.claim(PROFILES['music'],'fixture.music.worker')
  service_token='isolated-authority-token-1234567890123456789'
  authority=ThreadingHTTPServer(('127.0.0.1',0),handler(bridge,[service_token]));threading.Thread(target=authority.serve_forever,daemon=True).start()
  self.addCleanup(authority.server_close);self.addCleanup(authority.shutdown)
  source=Path(os.environ['MEDIA_PDS_SOURCE']);spec=importlib.util.spec_from_file_location('fixture_media_pds_authority',source/'data-service/tests/test_media_artifacts.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  fixture=m.Tests();fixture.setUp();self.addCleanup(fixture.tearDown)
  def authority_call(method,path,body):
   req=urllib.request.Request('http://127.0.0.1:'+str(authority.server_port)+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+service_token})
   with urllib.request.urlopen(req)as r:return json.load(r)
  fixture.media.authority=authority_call
  with tempfile.TemporaryDirectory()as temp:
   client=MediaArtifactClient('http://127.0.0.1:'+str(fixture.server.server_port),m.TOKEN)
   source=lambda c,w:dict(revision='0',projectId='binari',targetId='music.example',execution={},inputFiles=[])
   port=MusicWorkspace(Path(temp).resolve(),client,source,lambda *a:True);folder=port.stage(claim)
   (folder/'music.wav').write_bytes(base64.b64decode(m.packet()['artifact']['base64']));(folder/'music-result.json').write_text('{}')
   receipt=port.publish(claim,folder,(folder/'music.wav',folder/'music-result.json'));receipt.validate(claim)
   self.assertEqual(db.execute('select state from resident_publications').fetchone()[0],'committed')
   self.assertEqual(port.publish(claim,folder,(folder/'music.wav',folder/'music-result.json')),receipt)
   from workers.resident.adapters.image_pds import media_publication_verifier
   bridge.verify_publication=media_publication_verifier(client)
   bridge.complete(claim,receipt,True)
   self.assertEqual(c.row(claim.request_id)['state'],'completed')
