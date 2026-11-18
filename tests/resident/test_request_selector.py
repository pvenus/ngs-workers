import json,sqlite3,unittest
from types import SimpleNamespace
from workers.resident.deployment.request_selector import FormalPayloadSelector,digest
from workers.resident.contracts import PROFILES
class SelectorTests(unittest.TestCase):
 def setUp(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
  db.executescript('CREATE TABLE common_requests(request_id TEXT,request_type TEXT,state TEXT,retry_at REAL,created_at REAL);CREATE TABLE common_payload_refs(request_id TEXT,domain TEXT,schema_version TEXT,input_hash TEXT);')
  self.common=SimpleNamespace(db=db,clock=lambda:5)
  self.payload={'intent':'original'}
  for i in (1,2):
   db.execute('INSERT INTO common_requests VALUES(?,?,?,?,?)',(str(i),'test','queued',0,i))
   db.execute('INSERT INTO common_payload_refs VALUES(?,?,?,?)',(str(i),'domain','schema',digest(self.payload)))
 def selector(self,ready=lambda r:True,payload=None):
  def resolve(row,ref):return {'requestId':row['request_id'],'parameters':payload or self.payload,'inputRefs':['pds:source'],'expectedRevision':'1'}
  return FormalPayloadSelector(self.common,{('test','schema','domain'):('document.edit',resolve,lambda p:True)},ready=ready)
 def test_skips_dependency_blocked_without_blocking_next(self):
  item=self.selector(ready=lambda r:r['request_id']=='2')(PROFILES['document'])
  self.assertEqual(item['requestId'],'2');self.assertEqual(item['parameters'],self.payload)
 def test_hash_mismatch(self):
  with self.assertRaisesRegex(ValueError,'BINDING'):self.selector(payload={'intent':'changed'})(PROFILES['document'])
 def test_wrong_role_never_dispatches(self):self.assertIsNone(self.selector()(PROFILES['music']))
 def test_unknown_route_not_guessed(self):
  self.common.db.execute("UPDATE common_requests SET request_type='npc.workflow'")
  self.assertIsNone(self.selector()(PROFILES['document']))
