"""Observe an all-directions parent and register formal single-direction children.

No worker execution, global resource acquisition, ownership admission or model
call happens here. The regular Request authority grants each child video-global.
"""
import json
from threading import Event
from types import SimpleNamespace
from ..adapters.animation_pose import binding
from ..adapters.animation_context import read_context
from ..adapters.pds_frames import CONTEXT_READ_CONFLICTS
from ..adapters.animation_workspace import context_basis
from ..adapters.frame_workspace import digest
from ..backends.frame_postprocess import DIRECTIONS

SNAPSHOT_READ_CONFLICTS=CONTEXT_READ_CONFLICTS | frozenset(('ANIMATION_SOURCE_CHANGED','ANIMATION_CONTEXT_VERSION_CHANGED','ANIMATION_CONTEXT_CHANGED'))

class AnimationBatchLoop:
 def __init__(self,sources,common,service_root,client,original,admitted_request,poll_seconds=1):
  self.sources,self.common,self.root=sources,common,service_root
  self.client,self.original,self.admitted=client,original,admitted_request
  self.stop=Event();self.poll_seconds=poll_seconds;self.last_error=None
 def _ports(self):
  outer=self
  class Ports:
   def reference_snapshot(self,request):
    # A sibling publication can invalidate a catalog/read revision between
    # requests. Restart the entire read cohort, never reuse a stale catalog or
    # retry a publication. The caller still compares all pinned input hashes.
    for attempt in range(3):
     try:return self._reference_snapshot(request)
     except ValueError as exc:
      if str(exc) not in SNAPSHOT_READ_CONFLICTS or attempt==2:raise
   def _reference_snapshot(self,request):
    hint=SimpleNamespace(request_id=request['requestId'],capability='animation.generate',parameters={k:v for k,v in request.items()if k!='requestId'})
    port=outer.client(hint);current=port.current();npc=next(n for n in current['document']['characters']if n['id']==request['npcId'])
    views={d:binding(npc,{**request,'direction':d})for d in DIRECTIONS}
    context=read_context(npc,{**request,'direction':'n'},lambda method,path,payload=None:port.json(path,payload))
    return {'poseViews':views,'contextHash':digest(context_basis(context)),
      'npcInputHash':digest({k:v for k,v in npc.items()if k not in ('animationSlots','animationFrameSets')})}
   def verify_child_publication(self,request,view):
    body={k:v for k,v in request.items()if k!='requestId'}
    outer.original(request['requestId'],'animation.generate',body)
    hint=SimpleNamespace(request_id=request['requestId'],capability='animation.generate',parameters=body)
    port=outer.client(hint);receipt=port.receipt(request['requestId']);key='/'.join(request[k]for k in ('posePreset','presetId','direction'))
    if (view['status']!='completed' or receipt!=view.get('publication') or not receipt
      or receipt.get('npcId')!=request['npcId'] or receipt.get('slotKey')!=key):raise ValueError('ANIMATION_BATCH_PDS_PUBLICATION_CHANGED')
    current=port.current();npc=next(n for n in current['document']['characters']if n['id']==request['npcId']);slot=npc.get('animationSlots',{}).get(key,{})
    if slot.get('requestId')!=request['requestId'] or slot.get('assetId')!=receipt['assetId']:raise ValueError('ANIMATION_BATCH_CURRENT_SLOT_CHANGED')
    return receipt
  return Ports()
 def tick(self):
  results=[]
  rows=self.common.db.execute("SELECT * FROM common_requests WHERE request_type='npc.animation.generate-eight-directions' AND (state IN ('queued','retry_wait') OR (state='processing' AND worker='animation.external-batch' AND lease_until IS NOT NULL AND lease_until<=?)) AND retry_at<=? ORDER BY created_at,request_id",(self.common.clock(),self.common.clock())).fetchall()
  for row in rows:
   if self.admitted(dict(row))is not True:continue
   # Existing source contract is required. Never fall back to inline work().
   service=self.sources.modules['animation_service'].AnimationRequests(self.common,self.root,self._ports(),None,external_children=True)
   try:
    value=service.work(row['request_id'],row['requester']);results.append((row['request_id'],value['status']))
   except BlockingIOError:continue
   except Exception as exc:
    self.last_error=type(exc).__name__;results.append((row['request_id'],'blocked'))
  return results
 def serve(self):
  while not self.stop.is_set():
   try:self.tick()
   except Exception as exc:self.last_error=type(exc).__name__
   self.stop.wait(self.poll_seconds)
