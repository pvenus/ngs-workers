"""Advance accepted one-result NPC parents after resident child completion.

No new intake, model call, selection UI or direct original write. The existing
parent's finalizer alone performs its authorized single-result CAS publication.
"""
import json
from threading import Event
from .frame_workspace import digest

class CompletedOnlyExecutor:
 def __init__(self,delegate,common):self.delegate,self.common=delegate,common
 def __getattr__(self,name):return getattr(self.delegate,name)
 def prepare(self,*a,**kw):raise ValueError('DOCUMENT_AUTO_APPLY_CANNOT_GENERATE')
 def run(self,stage,child,actor,**kw):
  row=self.common.row(child)
  if row['state']!='completed'or row['requester']!=actor:raise ValueError('DOCUMENT_AUTO_APPLY_CHILD_NOT_COMPLETE')
  path=self.delegate.root/(child+'.receipt.json')
  if path.is_symlink()or not path.is_file():raise ValueError('DOCUMENT_AUTO_APPLY_RECEIPT_REQUIRED')
  receipt=json.loads(path.read_text())
  if (receipt.get('requestId')!=child or receipt.get('publicationScope')!='request-draft-candidate'
      or receipt.get('sourceApplied')is not False or receipt.get('verified')is not True
      or receipt.get('candidateCount')!=1):raise ValueError('DOCUMENT_AUTO_APPLY_DRAFT_INVALID')
  # Return saved receipt directly; never call the old model execution method.
  return receipt

class ParentAutoApply:
 def __init__(self,common,service_for_parent,*,poll_seconds=5,on_error=None):
  if not callable(service_for_parent)or poll_seconds<=0:raise ValueError('DOCUMENT_AUTO_APPLY_CONFIG')
  self.common,self.service_for_parent=common,service_for_parent
  self.poll_seconds,self.on_error=poll_seconds,on_error or (lambda rid,error:None)
  self.stop=Event()
 def candidates(self):
  rows=self.common.db.execute("SELECT b.* FROM npc_single_batches b JOIN common_requests r ON r.request_id=b.id WHERE r.state IN ('queued','retry_wait') AND r.retry_at<=? ORDER BY r.created_at,b.id",(self.common.clock(),)).fetchall()
  for raw in rows:
   row=dict(raw);selection=json.loads(row['selection_json']);request=json.loads(row['request_json'])
   if digest(request)!=row['input_hash']or selection.get('requestHash')!=row['input_hash']:continue
   if selection.get('registry',{}).get('selectionPolicy',{}).get('executionContract')!='worker-count.v1':continue
   stages=selection.get('stages',[]);invocations=selection.get('invocations',[])
   if len(stages)!=1 or stages[0].get('id')not in ('overview','art','category-add','category-modify')or stages[0].get('candidateCount')!=1 or len(invocations)!=1:continue
   calls=self.common.db.execute('SELECT * FROM npc_single_invocations WHERE parent_id=?',(row['id'],)).fetchall()
   if len(calls)!=1 or not calls[0]['child_id']:continue
   child=self.common.row(calls[0]['child_id'])
   if child['state']!='completed'or child['parent_id']!=row['id']or child['requester']!=row['actor']:continue
   # Only migrated resident drafts qualify, never arbitrary historical completed requests.
   proof=self.common.db.execute('SELECT 1 FROM resident_resource_grants WHERE request_id=?',(calls[0]['child_id'],)).fetchone()
   if proof is None:continue
   yield row
 def tick(self):
  results=[]
  for row in list(self.candidates()):
   if self.stop.is_set():break
   rid=row['id']
   try:
    service=self.service_for_parent(rid)
    # Factory supplies a fresh service object per call, not a shared live executor.
    service.executor=CompletedOnlyExecutor(service.executor,self.common)
    value=service.work(rid,row['actor'])
    state=value.get('common',{}).get('state')
    if state!='completed':raise ValueError('DOCUMENT_AUTO_APPLY_NOT_PUBLISHED')
    path=service.root/'ownership'/(digest(rid)+'.published.json')
    if path.is_symlink()or not path.is_file():raise ValueError('DOCUMENT_AUTO_APPLY_PUBLICATION_REQUIRED')
    proof=json.loads(path.read_text())
    if proof.get('requestId')!=rid or proof.get('result',{}).get('sourceApplied')is not True or proof.get('result',{}).get('publication',{}).get('verified')is not True:raise ValueError('DOCUMENT_AUTO_APPLY_PUBLICATION_UNVERIFIED')
    results.append({'requestId':rid,'state':'completed'})
   except Exception as error:
    self.on_error(rid,type(error).__name__);results.append({'requestId':rid,'state':'pending','error':type(error).__name__})
  return results
 def serve(self):
  while not self.stop.is_set():
   try:self.tick()
   except Exception as error:self.on_error(None,type(error).__name__)
   self.stop.wait(self.poll_seconds)
