"""Retry native ownership completion after durable Common/PDS completion.

Unknown or failed publication reservations are never turned into releases here;
Native Producer/FailureFence remain the authoritative proof readers.
"""
from threading import Event
from types import SimpleNamespace

class MediaOwnershipLoop:
 def __init__(self,common,tables,original,session_for,service_for,lifecycle,admitted,poll_seconds=1):
  self.common,self.tables,self.original=common,tables,original
  self.session_for,self.service_for,self.lifecycle=session_for,service_for,lifecycle
  self.admitted=admitted;self.stop=Event();self.poll_seconds=poll_seconds;self.last_error=None
 def tick(self):
  results=[]
  for capability,table in self.tables.items():
   rows=self.common.db.execute("SELECT c.* FROM common_requests c JOIN "+table+" i ON i.id=c.request_id WHERE c.state IN ('completed','blocked','failed','conflict') ORDER BY c.created_at,c.request_id").fetchall()
   for row in rows:
    if self.admitted(dict(row))is not True:continue
    try:
     hint=SimpleNamespace(request_id=row['request_id'],capability=capability,parameters=self.original(row['request_id'],capability))
     session=self.session_for(hint)
     if session.rid!=row['request_id']:continue # Batch parent owns shared lease.
     done=self.lifecycle.finalize(self.service_for(hint,session),session)
     results.append((row['request_id'],'released' if done else 'pending'))
    except Exception as exc:
     self.last_error=type(exc).__name__;results.append((row['request_id'],'pending'))
  return results
 def serve(self):
  while not self.stop.is_set():
   try:self.tick()
   except Exception as exc:self.last_error=type(exc).__name__
   self.stop.wait(self.poll_seconds)
