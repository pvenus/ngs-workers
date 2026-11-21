"""Read-only bridge from an unpublished, already released animation failure to formal fresh retry.

Uses the existing FailureFence/MediaOperationScope and terminal evidence reader. No PDS reads or writes are needed.
Never releases, resumes, cancels, publishes or generates anything.
"""
import json,re
from .failure_fence import digest
from ..contracts import Claim

class UnpublishedAnimationRetry:
 def __init__(self,failure,animation_lifecycle):
  self.failure,self.lifecycle=failure,animation_lifecycle
  self.common=failure.common
 def read(self,stage,rid,actor,key=None):
  if not isinstance(stage,str)or re.fullmatch(r'animation_(walk|run|combat_[0-9]+)',stage)is None:return None
  def need(ok):
   if not ok:raise ValueError('UNPUBLISHED_ANIMATION_RETRY_UNVERIFIED')
  prior=self.common.db.execute('SELECT proof_json FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone()
  if prior is None or json.loads(prior[0]).get('publicationNeverStarted')is not True:return None
  reader=getattr(self.lifecycle,'released_session',None)
  need(callable(reader))
  session=reader(rid)
  need(session.path.is_file()and not session.path.is_symlink())
  state=json.loads(session.path.read_text());guard=state.get('guard')
  need(isinstance(guard,dict)and state.get('released')is True and all(state.get(k)==guard.get(k)for k in ('npcId','generationId','requestId'))and guard.get('requestId')==rid)
  with self.failure.bridge.transaction():
   row=self.common.row(rid)
   need(row['requester']==actor and row['request_type']=='npc.animation.generate-eight-directions' and row['state']in ('blocked','failed','conflict'))
   membership=self.common.db.execute("SELECT a.*,p.requester,p.source,p.request_type FROM npc_one_stop_attempts a JOIN common_requests p ON p.request_id=a.parent_id WHERE a.child_id=? AND a.stage_id=?",(rid,stage)).fetchall()
   need(len(membership)==1 and membership[0]['requester']==actor and membership[0]['source']=='npc-one-stop'and membership[0]['request_type']=='npc.workflow')
   member=membership[0]
   need(self.common.db.execute('SELECT MAX(attempt) FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=?',(member['parent_id'],stage)).fetchone()[0]==member['attempt'])
   scoped=self.lifecycle.scope(guard)
   snapshot=self.failure._operation_snapshot(rid,scoped)
   saved=self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone();need(saved is not None)
   proof=json.loads(saved['proof_json'])
   fingerprint=digest(snapshot)
   need(saved['snapshot_hash']==fingerprint==proof.get('snapshotHash'))
   need(proof.get('ledgerProofHash')==digest({k:v for k,v in proof.items()if k!='ledgerProofHash'}))
   need(all(proof.get(k)==guard[k]for k in ('npcId','generationId','requestId'))and proof.get('state')==row['state']and proof.get('revision')==row['revision'])
   need(proof.get('publicationNeverStarted')is True and proof.get('unresolvedReservations')==0)
   need(not snapshot['publications'] and not snapshot['checkpoints'])
   need(not any(r.get('source_receipt')or r.get('publication_receipt')for r in snapshot['requests']))
   need(proof.get('allExecutionsStopped')is True or (proof.get('executionClosureKind')=='animation-timeout-quiescent.v1' and proof.get('allExecutionsClosed')is True))
   need(all(proof.get(k)is True for k in ('publicationFenced','descendantsIneligible')))
   need(all(g['released']==1 for g in snapshot['grants']))
   fences=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need({f['request_id']for f in fences}=={r['request_id']for r in snapshot['requests']}and all(f['proof_hash']==proof['ledgerProofHash']for f in fences))
  # Independently verify actual terminal proof; no publication exists to read.
  for grant in snapshot['grants']:
   raw=json.loads(grant['claim_json']);raw['input_refs']=tuple(raw['input_refs'])
   closure=self.failure.bridge.execution_closure(Claim(**raw));need(closure is not None)
   if proof.get('executionClosureKind'):need(closure in proof.get('executionClosures',[]))
  with self.failure.bridge.transaction():
   need(json.loads(session.path.read_text())==state)
   need(self.common.db.execute('SELECT MAX(attempt) FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=?',(member['parent_id'],stage)).fetchone()[0]==member['attempt'])
   latest_member=self.common.db.execute('SELECT child_id FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? AND attempt=?',(member['parent_id'],stage,member['attempt'])).fetchone()
   need(latest_member is not None and latest_member[0]==rid)
   need(digest(self.failure._operation_snapshot(rid,self.lifecycle.scope(guard)))==fingerprint)
   latest=self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone()
   need(latest is not None and dict(latest)==dict(saved))
   current=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need([dict(f)for f in current]==[dict(f)for f in fences])
  return {'schemaVersion':'npc-unpublished-animation-failure-retry.v1','requestId':rid,'actorId':actor,'stageId':stage,
          'ownershipReleased':True,'artifactsPreserved':True,'publicationNeverStarted':True,
          'snapshotHash':fingerprint,'ledgerProofHash':proof['ledgerProofHash'],'guardHash':digest(guard)}
