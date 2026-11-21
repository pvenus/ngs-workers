"""Request-owned register/wait/finalize loop for migrated one-result parents.

Requires the reviewed NGS external_children mode. Never wraps or calls inline run.
"""
import json
from threading import Event
from .frame_workspace import digest
class ExternalParentRunner:
 def __init__(self,common,service_for_parent,*,owns_parent,poll_seconds=5,failure_finalizer=None):
  if not callable(service_for_parent)or not callable(owns_parent):raise ValueError('EXTERNAL_PARENT_PORTS_REQUIRED')
  self.common,self.service_for_parent,self.owns_parent=common,service_for_parent,owns_parent
  self.poll_seconds=poll_seconds;self.stop=Event();self.failure_finalizer=failure_finalizer
 def tick(self):
  rows=self.common.db.execute("SELECT b.* FROM npc_single_batches b JOIN common_requests r ON r.request_id=b.id WHERE r.state IN ('queued','retry_wait','blocked','failed','conflict') AND r.retry_at<=? ORDER BY r.created_at",(self.common.clock(),)).fetchall()
  results=[]
  for row in rows:
   row=dict(row)
   if not self.owns_parent(row):continue
   request=json.loads(row['request_json']);selection=json.loads(row['selection_json'])
   if digest(request)!=row['input_hash']or selection.get('requestHash')!=row['input_hash']:raise ValueError('EXTERNAL_PARENT_INPUT_CHANGED')
   service=self.service_for_parent(row['id'])
   if getattr(service,'external_children',False)is not True:raise ValueError('EXTERNAL_PARENT_INLINE_FORBIDDEN')
   # Existing source work performs child registration OR waiting OR finalization.
   # No alternate worker path, force-resume, state rewrite or source mutation here.
   try:
    if self.common.row(row['id'])['state']in ('blocked','failed','conflict'):
     if self.failure_finalizer is not None:results.append(self.failure_finalizer(service,row['id'],row['actor']))
     continue
    result=service.work(row['id'],row['actor']);results.append(result)
   except Exception as error:
    if self.failure_finalizer is not None and self.common.row(row['id'])['state']in ('blocked','failed','conflict'):
     try:self.failure_finalizer(service,row['id'],row['actor'])
     except Exception:pass # Keep failed state/ownership when proof remains unknown.
    results.append({'requestId':row['id'],'state':'pending','error':type(error).__name__})
  return results
 def serve(self):
  while not self.stop.is_set():
   self.tick();self.stop.wait(self.poll_seconds)
