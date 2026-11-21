"""Trusted, frame-only proof of a failed publish call before its durable intent.

Runtime attempt markers are retained. Model terminal proof alone is insufficient:
this also requires current publisher-service quiescence and exact server absence.
"""
import json
from .failure_fence import digest
from ..contracts import Claim

class FrameNoPostReader:
 def __init__(self,bridge,read,observe):
  if not callable(read)or not callable(observe):raise ValueError('FRAME_NO_POST_PORTS')
  self.bridge,self.read,self.observe=bridge,read,observe
 def __call__(self,snapshot):
  def need(v):
   if not v:raise ValueError('FRAME_NO_POST_UNPROVEN')
  need(len(snapshot['requests'])==1 and len(snapshot['grants'])==1 and 'frameIntake'in snapshot)
  row=snapshot['requests'][0];g=snapshot['grants'][0];raw=json.loads(g['claim_json']);raw['input_refs']=tuple(raw['input_refs']);claim=Claim(**raw)
  need(row['request_type']=='npc.animation.frames.generate'and row['parent_id']is None and row['state']in('blocked','failed','conflict')and row['worker']is None)
  need(claim.capability=='frames.extract'and claim.parameters.get('scalePreset','full')=='full'and claim.request_id==row['request_id']and claim.fencing_token==str(row['fence'])and g['released']==1)
  need(self.bridge.verify_stopped(claim)is True)
  before=self.observe(claim);first=self.read(claim);second=self.read(claim);after=self.observe(claim)
  # Observer supplies a stable, typed identity, not a caller success boolean.
  need(isinstance(before,dict)and before==after and before.get('schemaVersion')=='resident.postprocess-quiescence.v1'and before.get('claimHash')==digest(raw)and before.get('serviceStopped')is True and before.get('processesAbsent')is True and isinstance(before.get('serviceIdentity'),str)and len(before['serviceIdentity'])==64)
  need(first==second and isinstance(first,dict)and first.get('requestId')==claim.request_id and first.get('claimHash')==digest(raw)and first.get('receiptState')=='authoritative-not-found'and first.get('intentAbsent')is True)
  need(all(isinstance(first.get(k),str)and len(first[k])==64 for k in ('workspaceHash','manifestHash','sourceHash','guardHash')))
  value={'schemaVersion':'resident.frame-no-post.v1','snapshotHash':digest(snapshot),'grantId':claim.resource_grant,'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'claimHash':digest(raw),'provenance':first,'quiescence':before}
  return {**value,'evidenceHash':digest(value)}
