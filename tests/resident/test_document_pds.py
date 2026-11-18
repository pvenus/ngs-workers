import json,tempfile,unittest
from pathlib import Path
from workers.resident.adapters.document_pds import DocumentDraftWorkspace,digest
from workers.resident.contracts import Claim
class DraftTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.c=Claim('req','attempt','fence','document.edit',(),{'intent':'edit'},'grant','1')
  class Stager:
   def stage(self,claim,folder):pass
  class Drafts:
   def authorize(self,claim):return {'requestId':claim.request_id,'publicationScope':'request-draft-candidate','parentOwnsApply':True}
   def submit(self,claim,packet):self.packet=packet
   def read(self,claim):return {'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'verified':True,'packetHash':digest(self.packet),'publicationScope':'request-draft-candidate','sourceApplied':False,'draftRef':'pds:draft','revision':'2'}
  self.drafts=Drafts();self.p=DocumentDraftWorkspace(self.root,Stager(),self.drafts)
 def test_draft_not_canonical_and_cleanup(self):
  folder=self.p.stage(self.c);out=folder/'response.json';out.write_text('{"proposals":[]}')
  receipt=self.p.publish(self.c,folder,(out,));receipt.validate(self.c)
  self.assertFalse(self.drafts.packet['sourceApplied']);self.assertTrue(self.p.cleanup(self.c,folder))
 def test_existing_attempt_no_reexecution(self):
  self.p.stage(self.c)
  with self.assertRaisesRegex(RuntimeError,'EXISTING'):self.p.stage(self.c)
 def test_missing_parent_authority(self):
  self.drafts.authorize=lambda c:{'requestId':c.request_id,'parentOwnsApply':False}
  with self.assertRaisesRegex(ValueError,'AUTHORITY'):self.p.stage(self.c)
  self.assertEqual(list(self.root.iterdir()),[])
 def test_claim_tamper_prevents_cleanup(self):
  folder=self.p.stage(self.c);(folder/'claim.json').write_text('{}')
  with self.assertRaisesRegex(ValueError,'OWNER'):self.p.cleanup(self.c,folder)
  self.assertTrue(folder.exists())
