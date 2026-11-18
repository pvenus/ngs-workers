import json,sqlite3,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from workers.resident.adapters.document_parent_bridge import ParentDraftBridge,digest
from workers.resident.contracts import Claim
class ParentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
  self.db.executescript('CREATE TABLE common_payload_refs(request_id,domain,schema_version,input_hash);CREATE TABLE npc_single_batches(id,actor,request_json,input_hash);')
  self.request={'schemaVersion':'npc-category-request.v1','projectId':'binari','npcId':'npc','baseRevision':1,'mode':'modify','categoryId':'overview','intent':'update overview','candidateCount':1}
  self.binding={'variation':None,'stage':{'id':'overview','candidateCount':1},'request':self.request,'childKey':'key','actor':'owner','parentId':'parent'}
  self.record={**self.binding,'localRequestId':'local'};(self.root/'child.json').write_text(json.dumps(self.record))
  self.db.execute('INSERT INTO common_payload_refs VALUES(?,?,?,?)',('child','npc.category-candidate','ngs.npc-category-candidate.v1',digest(self.binding)))
  self.db.execute('INSERT INTO npc_single_batches VALUES(?,?,?,?)',('parent','owner',json.dumps(self.request),digest(self.request)))
  self.common=SimpleNamespace(db=self.db,row=lambda rid:{'request_type':'npc.workflow','requester':'owner','parent_id':'parent'})
  self.claim=Claim('child','attempt','fence','document.edit',('child',),self.binding,'grant','1')
  self.proposal={'title':'title','categoryName':'overview','content':'after','rationale':'r','evidenceSummary':'e','differences':[],'assumptions':[],'unresolved':[]}
  self.state={'state':'ready','proposals':None,'inputs':self.request,'requestHash':'inputhash','applied':None,'selection':None}
  outer=self
  class Category:
   def __init__(self,g):self.g=g
   def get(self,_):return outer.state
   def prepare(self,_):
    p=self.g({'request':outer.request});outer.state.update(state='awaiting_selection',proposals={'proposals':p},proposalHash='hash');return outer.state
   def close(self):pass
  self.executor=SimpleNamespace(root=self.root,_record=lambda rid:self.root/(rid+'.json'),_category=lambda g:(SimpleNamespace(close=lambda:None,get=lambda _: {'state':outer.state['state']}),Category(g)))
  class Dispatch:
   def __init__(self,p,c):self.c=c
   def once(self,*a,**kw):return self.c.prepare(kw['local_id'])
  self.bridge=ParentDraftBridge(self.common,self.executor,lambda c:True,Dispatch,lambda c:nullcontext())
 def test_original_envelope_and_parent_draft(self):
  selected=self.bridge.payload({'request_id':'child','project_id':'binari'},{'reference_id':'child'})
  self.assertEqual(selected['parameters'],self.binding)
  parent=self.bridge.authorize(self.claim)
  packet={'requestId':'child','attemptId':'attempt','fencingToken':'fence','publicationScope':'request-draft-candidate','parent':parent,'requestHash':digest(self.binding),'sourceApplied':False,'output':{'proposals':[self.proposal]}}
  self.bridge.submit(self.claim,packet);self.bridge.submit(self.claim,packet)
  receipt=self.bridge.read(self.claim)
  self.assertFalse(receipt['sourceApplied']);self.assertEqual(self.state['state'],'awaiting_selection')
  self.assertTrue((self.root/'child.receipt.json').is_file());self.assertIsNone(self.state['applied'])
 def test_parent_changed_blocks_before_category(self):
  self.db.execute("UPDATE npc_single_batches SET input_hash='changed'")
  with self.assertRaisesRegex(ValueError,'PARENT_REQUEST'):self.bridge.authorize(self.claim)
  self.assertEqual(self.state['state'],'ready')
 def test_envelope_changed(self):
  self.db.execute("UPDATE common_payload_refs SET input_hash='changed'")
  with self.assertRaisesRegex(ValueError,'BINDING'):self.bridge.authorize(self.claim)
 def test_real_category_service_preserves_parent_selection_boundary(self):
  import os,sys
  source=os.environ.get('NGS_RUNTIME_SOURCE')
  if not source:self.skipTest('requires explicit read-only NGS source')
  sys.path.insert(0,source)
  from local.services.npc_category.service import CategoryService
  inputs={'featureId':'npc.category.request','schemaVersion':'npc-category-request.v1','mode':'modify','npcId':'npc','categoryId':'overview','baseRevision':1,'requester':'owner','intent':'update overview','candidateCount':1,'incarnationId':'created.1'}
  class Adapter:
   def read(self,project,npc):return {'revision':1,'character':{'creationBasis':{'requestId':'created.1'}},'categories':[{'id':'overview','name':'overview','content':'before'}],'source':{'transport':'fixture-pds'}}
   def apply(self,*a):raise AssertionError('parent alone applies')
  adapter=Adapter();root=self.root/'category'
  def category(generator):return CategoryService(root,adapter,generator,lambda *a:(_ for _ in ()).throw(AssertionError('no publish')))
  c=category(None);c.accept('local','binari',inputs);c.close()
  self.executor._category=lambda g:(SimpleNamespace(close=lambda:None,get=lambda _: {'state':'ready'}),category(g))
  authority=self.bridge.authorize(self.claim)
  packet={'requestId':'child','attemptId':'attempt','fencingToken':'fence','publicationScope':'request-draft-candidate','parent':authority,'requestHash':digest(self.binding),'sourceApplied':False,'output':{'proposals':[self.proposal]}}
  self.bridge.submit(self.claim,packet)
  c=category(None)
  try:
   saved=c.get('local');self.assertEqual(saved['state'],'awaiting_selection');self.assertIsNone(saved['applied']);self.assertIsNone(saved['selection']);self.assertEqual(saved['proposals']['proposals'],[self.proposal])
  finally:c.close()
 def test_real_common_claim_and_draft_completion_preserve_parent(self):
  import os,sys,threading
  source=os.environ.get('NGS_RUNTIME_SOURCE')
  if not source:self.skipTest('requires explicit NGS source')
  sys.path.insert(0,source)
  from local.services.request_pipeline.common import CommonRequests
  from workers.resident.adapters.common_authority import CommonAuthorityBridge
  from workers.resident.contracts import PROFILES,Receipt
  common=CommonRequests(':memory:');self.addCleanup(common.close)
  common.db.execute('CREATE TABLE npc_single_batches(id,actor,request_json,input_hash)')
  common.db.execute('INSERT INTO npc_single_batches VALUES(?,?,?,?)',('parent','owner',json.dumps(self.request),digest(self.request)))
  common.accept({'requestId':'child','projectId':'binari','requestType':'npc.workflow','schemaVersion':'ngs.npc-category-candidate.v1','source':'npc.workflow','requester':'owner','idempotencyKey':'key','parentRequestId':'parent','payloadRef':{'domain':'npc.category-candidate','schemaVersion':'ngs.npc-category-candidate.v1','referenceId':'child','inputHash':digest(self.binding)}})
  self.bridge.common=common
  selected={'requestId':'child','requestType':'npc.workflow','capability':'document.edit','parameters':self.binding,'inputHash':digest(self.binding),'inputRefs':['child'],'expectedRevision':'1','binding':{'projectId':'binari','targetId':'npc','expectedRevision':'1'}}
  authority=CommonAuthorityBridge(common,threading.RLock(),lambda p:selected,self.bridge.verify_publication,{'video-global':1,'codex':1},verify_stopped=lambda c:True)
  authority.initialize_schema();claim=authority.claim(PROFILES['document'],'document.worker')
  self.bridge.authorize_claim=lambda c:authority.authorize_publication(c)
  parent=self.bridge.authorize(claim)
  packet={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'publicationScope':'request-draft-candidate','parent':parent,'requestHash':digest(self.binding),'sourceApplied':False,'output':{'proposals':[self.proposal]}}
  self.bridge.submit(claim,packet);r=self.bridge.read(claim)
  receipt=Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,(r['draftRef'],),r['revision'],True)
  authority.complete(claim,receipt,True)
  self.assertEqual(common.row('child')['state'],'completed')
  self.assertEqual(json.loads(common.row('child')['publication_receipt'])['receiptHash'],digest(r['parentReceipt']))
  self.assertFalse(r['parentReceipt']['sourceApplied'])
  self.assertIsNone(self.state['applied']);self.assertEqual(self.state['state'],'awaiting_selection')
