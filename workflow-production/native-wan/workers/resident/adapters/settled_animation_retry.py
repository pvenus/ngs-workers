"""Read-only bridge from an already released animation failure to formal fresh retry.

Uses the existing FailureFence/MediaOperationScope and live PDS receipt reader.
Never releases, resumes, cancels, publishes or generates anything.
"""
import json,re
from .failure_fence import digest
from ..contracts import Claim

class SettledAnimationRetry:
 def __init__(self,failure,animation_lifecycle):
  self.failure,self.lifecycle=failure,animation_lifecycle
  self.common=failure.common
 def read(self,stage,rid,actor,key=None):
  if not isinstance(stage,str)or re.fullmatch(r'animation_(walk|run|combat_[0-9]+)',stage)is None:return None
  def need(ok):
   if not ok:raise ValueError('SETTLED_ANIMATION_RETRY_UNVERIFIED')
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
   need(row['requester']==actor and row['request_type']=='npc.animation.generate-eight-directions' and row['state']in ('blocked','failed','conflict'))
   membership=self.common.db.execute("SELECT a.*,p.requester,p.source,p.request_type FROM npc_one_stop_attempts a JOIN common_requests p ON p.request_id=a.parent_id WHERE a.child_id=? AND a.stage_id=?",(rid,stage)).fetchall()
   need(len(membership)==1 and membership[0]['requester']==actor and membership[0]['source']=='npc-one-stop'and membership[0]['request_type']=='npc.workflow')
   member=membership[0]
   need(self.common.db.execute('SELECT MAX(attempt) FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=?',(member['parent_id'],stage)).fetchone()[0]==member['attempt'])
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
   mixed=(proof.get('allExecutionsStopped')is False and proof.get('allExecutionsClosed')is True and proof.get('executionClosureFamily')=='animation' and proof.get('executionClosureKind')=='animation-timeout-quiescent.v1')
   need((proof.get('allExecutionsStopped')is True or mixed) and all(proof.get(k)is True for k in ('publicationFenced','descendantsIneligible')))
   need(all(g['released']==1 for g in snapshot['grants']))
   fences=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need({f['request_id']for f in fences}=={r['request_id']for r in snapshot['requests']}and all(f['proof_hash']==proof['ledgerProofHash']for f in fences))
  # Independently verify current canonical publications and actual terminal proof.
  settled=self.failure._settled_publications(snapshot)
  need(bool(settled)and settled==proof.get('settledPublications'))
  closures=[]
  for grant in snapshot['grants']:
   raw=json.loads(grant['claim_json']);raw['input_refs']=tuple(raw['input_refs']);claim=Claim(**raw)
   if not mixed:
    need(self.failure.bridge.verify_stopped(claim)is True)
    continue
   closure=self.failure.bridge.execution_closure(claim)
   need(isinstance(closure,dict) and closure.get('grantId')==grant['grant_id'] and closure.get('kind') in ('terminal','timeout-service-quiescent'))
   closures.append(closure)
  if mixed:
   terminal={c['grantId'] for c in closures if c['kind']=='terminal'}
   need(closures==proof.get('executionClosures') and any(c['kind']=='timeout-service-quiescent' for c in closures) and len({c['grantId'] for c in closures})==len(closures) and all(x['grantId'] in terminal for x in settled))
  with self.failure.bridge.transaction():
   need(json.loads(session.path.read_text())==state)
   need(self.common.db.execute('SELECT MAX(attempt) FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=?',(member['parent_id'],stage)).fetchone()[0]==member['attempt'])
   current_member=self.common.db.execute('SELECT child_id FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? AND attempt=?',(member['parent_id'],stage,member['attempt'])).fetchone()
   need(current_member is not None and current_member[0]==rid)
   need(digest(self.failure._operation_snapshot(rid,self.lifecycle.scope(guard)))==fingerprint)
   latest=self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?',(rid,)).fetchone()
   need(latest is not None and dict(latest)==dict(saved))
   current=self.common.db.execute('SELECT * FROM resident_failure_fences WHERE parent_request_id=?',(rid,)).fetchall()
   need([dict(f)for f in current]==[dict(f)for f in fences])
  return {'schemaVersion':'npc-settled-animation-failure-retry.v1','requestId':rid,'actorId':actor,'stageId':stage,
          'ownershipReleased':True,'artifactsPreserved':True,'publicationSettled':True,
          'snapshotHash':fingerprint,'ledgerProofHash':proof['ledgerProofHash'],'guardHash':digest(guard)}
