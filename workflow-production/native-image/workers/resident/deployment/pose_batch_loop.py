"""Bounded official pose-parent observer. No image execution or resource claims."""
from threading import Event

class PoseBatchLoop:
 def __init__(self,common,service_for_parent,admitted_request,poll_seconds=1):
  self.common,self.service_for_parent,self.admitted=common,service_for_parent,admitted_request
  self.poll_seconds=poll_seconds;self.stop=Event();self.last_error=None
 def tick(self):
  rows=self.common.db.execute("SELECT * FROM common_requests WHERE request_type IN ('npc.pose.expand-eight-directions','npc.pose.regenerate-one') AND (state IN ('queued','retry_wait') OR (state='processing' AND worker='pose.parent-observer' AND lease_until IS NOT NULL AND lease_until<=?)) AND retry_at<=? ORDER BY created_at,request_id",(self.common.clock(),self.common.clock())).fetchall()
  results=[]
  for row in rows:
   if self.admitted(dict(row)) is not True:continue
   service=self.service_for_parent(row['request_id'])
   if getattr(service,'external_children',False)is not True:raise ValueError('POSE_EXTERNAL_PARENT_REQUIRED')
   try:
    value=service.work(row['request_id'],row['requester']);results.append((row['request_id'],value['status']))
   except BlockingIOError:continue
   except Exception as exc:self.last_error=type(exc).__name__;results.append((row['request_id'],'blocked'))
  return results
 def serve(self):
  while not self.stop.is_set():
   try:self.tick()
   except Exception as exc:self.last_error=type(exc).__name__
   self.stop.wait(self.poll_seconds)
