import json,sqlite3,unittest
from types import SimpleNamespace
from workers.resident.deployment.domain_resolvers import DomainResolvers,ANIMATION
from workers.resident.deployment.request_selector import digest
class ResolverTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
  self.db.execute('CREATE TABLE animation_intake_requests(id,actor,input_hash,body)')
  self.dto={'npcId':'npc','intent':'original'};self.h=digest(self.dto)
  self.db.execute('INSERT INTO animation_intake_requests VALUES(?,?,?,?)',('id','owner',self.h,json.dumps(self.dto)))
  self.reader=DomainResolvers(SimpleNamespace(db=self.db),lambda r,p:{'projectId':'binari','targetId':'npc','expectedRevision':'5','inputRefs':['pds:asset:hash']})
  self.row={'request_id':'id','project_id':'binari','requester':'owner'};self.ref={'reference_id':'id','input_hash':self.h}
 def test_original_body_and_separate_binding(self):
  v=self.reader.animation(self.row,self.ref);self.assertEqual(v['parameters'],self.dto);self.assertEqual(v['binding']['expectedRevision'],'5')
 def test_wrong_owner(self):
  with self.assertRaisesRegex(ValueError,'OWNER'):self.reader.animation({**self.row,'requester':'other'},self.ref)
 def test_payload_changed(self):
  self.db.execute("UPDATE animation_intake_requests SET body='{}'")
  with self.assertRaisesRegex(ValueError,'HASH'):self.reader.animation(self.row,self.ref)
 def test_required_validator(self):
  with self.assertRaisesRegex(ValueError,'VALIDATOR'):self.reader.selector({},lambda _:True,[ANIMATION])
