import json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from workers.resident.adapters.document_auto_apply import ParentAutoApply,digest
class AutoApplyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
  db.executescript('CREATE TABLE common_requests(request_id,state,retry_at,created_at,parent_id,requester);CREATE TABLE npc_single_batches(id,actor,request_json,selection_json,input_hash);CREATE TABLE npc_single_invocations(parent_id,child_id);CREATE TABLE resident_resource_grants(request_id);')
  self.common=SimpleNamespace(db=db,clock=lambda:5,row=lambda rid:db.execute('SELECT * FROM common_requests WHERE request_id=?',(rid,)).fetchone())
  request={'intent':'accepted request'};selection={'registry':{'selectionPolicy':{'executionContract':'worker-count.v1'}},'requestHash':digest(request),'stages':[{'id':'overview','candidateCount':1}],'invocations':[{'id':'overview.1'}]}
  db.execute('INSERT INTO npc_single_batches VALUES(?,?,?,?,?)',('parent','owner',json.dumps(request),json.dumps(selection),digest(request)))
  db.execute('INSERT INTO common_requests VALUES(?,?,?,?,?,?)',('parent','queued',0,0,None,'owner'))
  db.execute('INSERT INTO common_requests VALUES(?,?,?,?,?,?)',('child','completed',0,0,'parent','owner'))
  db.execute('INSERT INTO npc_single_invocations VALUES(?,?)',('parent','child'));db.execute('INSERT INTO resident_resource_grants VALUES(?)',('child',))
  self.receipt={'requestId':'child','state':'completed','publicationScope':'request-draft-candidate','sourceApplied':False,'verified':True,'candidateCount':1}
  (self.root/'child.receipt.json').write_text(json.dumps(self.receipt));self.applied=[]
  outer=self
  class Service:
   def __init__(self):self.root=outer.root;self.executor=SimpleNamespace(root=outer.root,run=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not generate')))
   def work(self,rid,actor):
    receipt=self.executor.run({},'child',actor)
    outer.applied.append((rid,actor,receipt)) # fixture for original parent CAS finalizer
    (outer.root/'ownership').mkdir(exist_ok=True)
    (outer.root/'ownership'/(digest(rid)+'.published.json')).write_text(json.dumps({'requestId':rid,'result':{'sourceApplied':True,'publication':{'verified':True}}}))
    db.execute("UPDATE common_requests SET state='completed' WHERE request_id=?",(rid,))
    return {'common':{'state':'completed'}}
  self.loop=ParentAutoApply(self.common,lambda rid:Service())
 def test_automatic_parent_no_selection_or_model(self):
  self.assertEqual(self.loop.tick(),[{'requestId':'parent','state':'completed'}]);self.assertEqual(len(self.applied),1)
  self.assertEqual(self.loop.tick(),[])
 def test_pending_child_waits(self):
  self.common.db.execute("UPDATE common_requests SET state='processing' WHERE request_id='child'")
  self.assertEqual(self.loop.tick(),[]);self.assertEqual(self.applied,[])
 def test_unmigrated_draft_excluded(self):
  self.common.db.execute('DELETE FROM resident_resource_grants')
  self.assertEqual(self.loop.tick(),[])
 def test_no_fallback_generation_without_receipt(self):
  (self.root/'child.receipt.json').unlink()
  self.assertEqual(self.loop.tick()[0]['state'],'pending');self.assertEqual(self.applied,[])
