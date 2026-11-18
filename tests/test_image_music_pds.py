import hashlib,tempfile,unittest
from pathlib import Path
from workers.resident.adapters.image_pds import ImageWorkspace
from workers.resident.adapters.music_pds import MusicWorkspace
from workers.resident.backends.image_music_workspace import digest
from workers.resident.contracts import Claim
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.claim=Claim('fixture.request','fixture.attempt','fixture.fence','image.generate',(),{'prompt':'original'},'grant','1')
 def test_staging_and_publication_exact_readback(self):
  from types import SimpleNamespace
  import base64
  state={};clean=[]
  def publish(p):
   state['data']=base64.b64decode(p['artifact']['base64']);state['r']={k:v for k,v in p.items()if k not in ('provenance','artifact','claim')}
   state['r'].update(status='committed',authority='filex',verified=True,receiptHash='a'*64,artifact={k:v for k,v in p['artifact'].items()if k!='base64'})
   state['r']['artifact']['assetId']='fixture.asset';state['r']['publicationHash']=digest(p);state['r']['receiptHash']=digest({k:v for k,v in state['r'].items()if k!='receiptHash'});return state['r']
  client=SimpleNamespace(publish=publish,receipt=lambda c:state['r'],asset=lambda a:state['data'])
  source=lambda c,w:dict(revision=1,projectId='project',targetId='target',execution={},inputFiles=[])
  port=ImageWorkspace(self.root,client,source,lambda c,w:clean.append(w)or True)
  root=port.stage(self.claim);(root/'out.png').write_bytes(b'fixture');(root/'metadata.json').write_text('{}')
  receipt=port.publish(self.claim,root,(root/'out.png',root/'metadata.json'));receipt.validate(self.claim)
  self.assertEqual(receipt.asset_refs,('fixture.asset',));self.assertEqual(port.recover(self.claim),receipt);self.assertTrue(port.cleanup(self.claim,root))
  with self.assertRaises(Exception):port.stage(self.claim)
 def test_changed_revision_fails_before_provider(self):
  source=lambda c,w:dict(revision=2,projectId='project',targetId='target',execution={},inputFiles=[])
  port=ImageWorkspace(self.root,None,source,lambda *a:True)
  with self.assertRaisesRegex(ValueError,'REVISION_CHANGED'):port.stage(self.claim)
 def test_input_path_escape_refused(self):
  source=lambda c,w:dict(revision=1,projectId='project',targetId='target',execution={},inputFiles=[dict(ref='pds:x',path='../escape',sha256='x',bytes=b'x')])
  port=ImageWorkspace(self.root,None,source,lambda *a:True)
  with self.assertRaisesRegex(ValueError,'PATH_INVALID'):port.stage(self.claim)

 def test_portrait_source_reuses_original_prompt_and_rejects_stale_context(self):
  import copy
  from dataclasses import replace
  from workers.resident.adapters.image_pds import PortraitSource
  value={'name':'fixture'};context={'npcRevision':1,'characterHash':'hash','sourceVersion':'v','incarnationId':'created','categories':[{'id':'identity','value':value,'contentHash':digest(value)}]}
  provider={'providerId':'codex.image_gen','toolName':'image_gen.imagegen','arguments':{'prompt':'unchanged','transparent_background':True}}
  record={'schemaVersion':'npc-workflow-portrait-single.v1','context':context,'claim':{'index':0,'providerClaim':'native','requestHash':'original','providerRequest':provider,'providerRequestHash':digest(provider)},'request':{'character':{'npcId':'npc.fixture'}}}
  claim=replace(self.claim,parameters=record)
  source=PortraitSource(lambda r:copy.deepcopy(context),lambda aid:None)
  result=source(claim,self.root);self.assertEqual(result['execution']['providerClaim']['providerRequest'],provider)
  self.assertEqual(result['targetId'],'npc.fixture')
  source.context_reader=lambda r:{**context,'npcRevision':2}
  with self.assertRaisesRegex(ValueError,'CONTEXT_CHANGED'):source(claim,self.root)

 def test_recovery_rejects_claim_tamper_and_never_submits_missing_receipt(self):
  import json
  from types import SimpleNamespace
  from dataclasses import asdict
  from workers.resident.backends.image_music_workspace import write_new
  calls=[];client=SimpleNamespace(receipt=lambda c:None,publish=lambda p:calls.append(p))
  source=lambda c,w:dict(revision=1,projectId='project',targetId='target',execution={},inputFiles=[])
  port=ImageWorkspace(self.root,client,source,lambda *a:True);folder=port.stage(self.claim)
  self.assertIsNone(port.recover(self.claim));self.assertFalse(calls)
  with self.assertRaisesRegex(ValueError,'INTENT_REQUIRED'):port.resume_publication(self.claim,'a'*64)
  self.assertFalse(calls)
  packet={'requestId':self.claim.request_id,'attemptId':self.claim.attempt_id,'fencingToken':self.claim.fencing_token,'capability':self.claim.capability,'expectedRevision':'1','requestHash':digest(dict(self.claim.parameters)),'projectId':'project','targetId':'target','claim':asdict(self.claim),'artifact':{'mimeType':'image/png'},'provenance':{'model':'codex.image_gen'}}
  write_new(folder/'publication-intent.json',json.dumps(packet).encode())
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):port.resume_publication(self.claim,'0'*64)
  self.assertFalse(calls)
  self.assertIsNone(port.recover(self.claim));self.assertFalse(calls)
  saved=json.loads((folder/'claim.json').read_text());saved['fencing_token']='wrong';(folder/'claim.json').write_text(json.dumps(saved))
  with self.assertRaisesRegex(ValueError,'OWNER_MISMATCH'):port.recover(self.claim)
