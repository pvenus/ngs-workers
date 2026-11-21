"""Read-only bridge from an already released pose failure to formal fresh retry.

Uses the existing FailureFence/MediaOperationScope and live PDS receipt reader.
Never releases, resumes, cancels, publishes or generates anything.
"""
import json
from .failure_fence import digest
from ..contracts import Claim

class SettledPoseRetry:
 def __init__(self,failure,pose_lifecycle):
  self.failure,self.lifecycle=failure,pose_lifecycle
  self.common=failure.common
 def read(self,stage,rid,actor,key=None):
  if stage not in ('idle_8way','combat_8way'):return None
  def need(ok):
   if not ok:raise ValueError('SETTLED_POSE_RETRY_UNVERIFIED')
  prior=self.common.db.execute('SELECT proof_json FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone()
  if prior is None or json.loads(prior[0]).get('publicationSettled')is not True:return None
  reader=getattr(self.lifecycle,'released_session',None)
  need(callable(reader))
  session=reader(rid)
  need(session.path.is_file()and not session.path.is_symlink())
  state=json.loads(session.path.read_text());guard=state.get('guard')
  need(isinstance(guard,dict)and state.get('released')is True and all(state.get(k)==guard.get(k)for k in ('npcId','generationId','requestId'))and guard.get('requestId')==rid)
  with self.failure.bridge.transaction():
   row=self.common.row(rid)
   need(row['requester']==actor and row['request_type']=='npc.pose.expand-eight-directions' and row['state']in ('blocked','failed','conflict'))
   intake=self.common.db.execute('SELECT * FROM pose_intake_requests WHERE id=?',(rid,)).fetchone()
   need(intake is not None and json.loads(intake['body']).get('posePreset')==('idle'if stage=='idle_8way'else'combat'))
   scoped=self.lifecycle.scope(guard)
   snapshot=self.failure._operation_snapshot(rid,scoped)
   saved=self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone();need(saved is not None)
   proof=json.loads(saved['proof_json'])
   if proof.get('publicationNeverStarted')is True:return None
   fingerprint=digest(snapshot)
   need(saved['snapshot_hash']==fingerprint==proof.get('snapshotHash'))
   need(proof.get('ledgerProofHash')==digest({k:v for k,v in proof.items()if k!='ledgerProofHash'}))
   need(all(proof.get(k)==guard[k]for k in ('npcId','generationId','requestId'))and proof.get('state')==row['state']and proof.get('revision')==row['revision'])
   need(proof.get('publicationNeverStarted')is False and proof.get('publicationSettled')is True and proof.get('unresolvedReservations')==0)
   need(all(proof.get(k)is True for k in ('allExecutionsStopped','publicationFenced','descendantsIneligible')))
   need(all(g['released']==1 for g in snapshot['grants']))
   fences=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need({f['request_id']for f in fences}=={r['request_id']for r in snapshot['requests']}and all(f['proof_hash']==proof['ledgerProofHash']for f in fences))
  # Independently verify current canonical publications and actual terminal proof.
  settled=self.failure._settled_publications(snapshot)
  need(bool(settled)and settled==proof.get('settledPublications'))
  for grant in snapshot['grants']:
   raw=json.loads(grant['claim_json']);raw['input_refs']=tuple(raw['input_refs'])
   need(self.failure.bridge.verify_stopped(Claim(**raw))is True)
  with self.failure.bridge.transaction():
   need(json.loads(session.path.read_text())==state)
   need(digest(self.failure._operation_snapshot(rid,self.lifecycle.scope(guard)))==fingerprint)
   latest=self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone()
   need(latest is not None and dict(latest)==dict(saved))
   current=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need([dict(f)for f in current]==[dict(f)for f in fences])
  return {'schemaVersion':'npc-settled-failure-retry.v1','requestId':rid,'actorId':actor,'stageId':stage,
          'ownershipReleased':True,'artifactsPreserved':True,'publicationSettled':True,
          'snapshotHash':fingerprint,'ledgerProofHash':proof['ledgerProofHash'],'guardHash':digest(guard)}
