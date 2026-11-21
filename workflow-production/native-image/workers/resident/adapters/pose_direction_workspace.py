"""Direction files remain in PDS-owned scratch; HTTP submits manifest metadata only."""
import json,hashlib
from pathlib import Path
from dataclasses import asdict
from .portrait_pair_workspace import PortraitPairWorkspace
from .image_pds import ImageWorkspace
from ..backends.image_music_workspace import digest,safe_file,stage as validate_stage
from ..backends.pose_direction import FILES
from ..contracts import Receipt
SCOPE='registered-npc-pose-direction'
class PoseDirectionWorkspace(PortraitPairWorkspace):
 def stage(self,claim):
  if claim.capability!='image.generate'or claim.parameters.get('schemaVersion')!='ngs.pose-direction-request.v1':raise ValueError('POSE_REQUEST_REQUIRED')
  auth=self.client.authorize(claim)
  if auth.get('requestId')!=claim.request_id or auth.get('publicationScope')!=SCOPE or auth.get('requiredSourceApplied')is not True or auth.get('originalRecordHash')!=digest(dict(claim.parameters)):raise ValueError('POSE_AUTHORIZATION_INVALID')
  return ImageWorkspace.stage(self,claim)
 def packet(self,claim,workspace):
  root=Path(workspace)
  if root!=self.folder(claim):raise ValueError('POSE_WORKSPACE_MISMATCH')
  _,envelope,_=validate_stage(claim,root)
  if digest(json.loads(safe_file(root,'claim.json').read_text()))!=digest(asdict(claim)):raise ValueError('POSE_OWNER_MISMATCH')
  manifest=json.loads(safe_file(root,'artifacts/pose-manifest.json').read_text())
  expected={'schemaVersion':'resident.pose-direction.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'parentRequestId':claim.parameters['parentRequestId'],'direction':claim.parameters['direction'],'parentStateHash':digest(envelope['execution']['state'])}
  if any(manifest.get(k)!=v for k,v in expected.items())or set(manifest.get('files',{}))!=set(FILES):raise ValueError('POSE_MANIFEST_SCOPE')
  for name in FILES:
   raw=safe_file(root,'artifacts/'+name).read_bytes()
   if manifest['files'][name]!={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}:raise ValueError('POSE_FILE_HASH_CHANGED')
  return {'manifest':manifest}
 def publish(self,claim,workspace,artifacts):
  packet=self.packet(claim,workspace);expected={Path(workspace)/'artifacts'/name for name in ('pose-manifest.json',*FILES)}
  if len(artifacts)!=5 or set(map(Path,artifacts))!=expected:raise ValueError('POSE_ARTIFACT_PATHS')
  self._intent(claim,workspace,packet)
  self._submit(claim,workspace,packet)
  return self.verified(claim,packet,self.client.read(claim))
 def verified(self,claim,packet,value):
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'packetHash':digest(packet),'verified':True,'sourceApplied':True,'publicationScope':SCOPE}
  if any(value.get(k)!=v for k,v in expected.items()):raise ValueError('POSE_RECEIPT_SCOPE')
  source=value.get('sourceReceipt',{});refs=value.get('assetRefs')
  if refs!=[source.get('assetId')] or value.get('revision')!=str(source.get('revision'))or source.get('verified')is not True or source.get('requestId')!=claim.request_id:raise ValueError('POSE_RECEIPT_ASSETS')
  r=Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(refs),value['revision'],True);r.validate(claim);return r

class PoseDirectionSource:
 """Request supplies immutable metadata; media bytes come only from guarded PDS."""
 def __init__(self,service,asset_reader):self.service,self.asset_reader=service,asset_reader
 def __call__(self,claim,folder):
  pin=self.service.source(claim)
  if set(pin)!={'revision','projectId','targetId','execution','inputFiles'} or str(pin['revision'])!=claim.expected_revision:raise ValueError('POSE_SOURCE_ENVELOPE')
  rows=[]
  for row in pin['inputFiles']:
   if set(row)!={'ref','path','sha256','assetId'} or row['assetId']!=row['sha256']+'.png' or row['ref']!='pds:asset:'+row['assetId']:raise ValueError('POSE_SOURCE_ASSET_SCOPE')
   raw=self.asset_reader(row['assetId'])
   if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('POSE_SOURCE_BYTES_CHANGED')
   rows.append({k:row[k]for k in ('ref','path','sha256')}|{'bytes':raw})
  if tuple(x['ref']for x in rows)!=tuple(claim.input_refs):raise ValueError('POSE_SOURCE_REFS_CHANGED')
  return {**pin,'inputFiles':rows}
