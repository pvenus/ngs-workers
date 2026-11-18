"""Request-service lifecycle for reconciliation and parent auto-apply only.

OPS supervises this process; it does not tick domain workflows itself.
Authorities/SQLite connections must be safe for their dedicated service threads.
"""
from threading import Thread

class RequestBackgroundLoops:
 def __init__(self,*,reconciliation,parent_auto_apply):
  self.loops=(('request-reconciliation',reconciliation),('npc-parent-auto-apply',parent_auto_apply));self.threads=[]
 def start(self):
  if self.threads:raise ValueError('WORKER_BACKGROUND_ALREADY_STARTED')
  for name,loop in self.loops:
   if loop is None:continue
   if loop.stop.is_set():raise ValueError('WORKER_BACKGROUND_ALREADY_STOPPED')
   thread=Thread(target=loop.serve,name=name,daemon=False);thread.start();self.threads.append(thread)
 def close(self):
  for _,loop in self.loops:
   if loop is not None:loop.stop.set()
  for thread in self.threads:thread.join()
  self.threads=[]
 def __enter__(self):self.start();return self
 def __exit__(self,*a):self.close()
