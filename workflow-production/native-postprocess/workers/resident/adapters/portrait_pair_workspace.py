"""Pair staging via PDS source reader; publication is canonical, never a draft fallback."""
import hashlib,json
from pathlib import Path
from dataclasses import asdict
from .image_pds import ImageWorkspace
from ..backends.image_music_workspace import digest,safe_file,stage as validate_stage
from ..contracts import Receipt

FILES=('output.png','provider-proof.json','pixel-claim.json','pixel-source.png','pixel-output.png','pixel-result.json')
SCOPE='registered-npc-portrait-pair'
class PortraitPairWorkspace(ImageWorkspace):
 def __init__(self,root,client,source,cleanup,*,direct_pds=None):
  super().__init__(root,client,source,cleanup)
  self.direct_pds=direct_pds
 def _submit(self,claim,workspace,packet):
  if self.direct_pds is None:
   if self.client.submit(claim,packet)!={'accepted':True}:raise ValueError('PAIR_SUBMIT_UNCONFIRMED')
   return
  # Reservation and final acknowledgment carry metadata only. PNG bytes never
  # pass through Request; PDS independently validates and commits the pair.
  reservation=self.client.prepare(claim,packet)
  self.direct_pds.publish(claim,workspace,packet,reservation)
  if self.client.finish(claim,packet)!={'accepted':True}:raise ValueError('PAIR_FINISH_UNCONFIRMED')
 def stage(self,claim):
  if claim.capability!='image.generate' or claim.parameters.get('schemaVersion')!='npc-workflow-portrait-single.v1':raise ValueError('PAIR_REQUEST_REQUIRED')
  auth=self.client.authorize(claim)
  if auth.get('requestId')!=claim.request_id or auth.get('publicationScope')!=SCOPE or auth.get('requiredSourceApplied')is not True or auth.get('originalRecordHash')!=digest(dict(claim.parameters)):raise ValueError('PAIR_AUTHORIZATION_INVALID')
  return super().stage(claim)
 def packet(self,claim,workspace):
  root=Path(workspace)
  if root!=self.folder(claim):raise ValueError('PAIR_WORKSPACE_MISMATCH')
  validate_stage(claim,root)
  if digest(json.loads(safe_file(root,'claim.json').read_text()))!=digest(asdict(claim)):raise ValueError('PAIR_OWNER_MISMATCH')
  manifest=json.loads(safe_file(root,'artifacts/pair-manifest.json').read_text())
  expected={'schemaVersion':'resident.portrait-pair.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'originalRecordHash':digest(dict(claim.parameters))}
  if any(manifest.get(k)!=v for k,v in expected.items()) or set(manifest.get('files',{}))!=set(FILES):raise ValueError('PAIR_MANIFEST_SCOPE')
  for name in FILES:
   raw=safe_file(root,'artifacts/'+name).read_bytes()
   if manifest['files'][name]!={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}:raise ValueError('PAIR_FILE_HASH_CHANGED')
  return {'manifest':manifest}
 def publish(self,claim,workspace,artifacts):
  packet=self.packet(claim,workspace)
  expected={Path(workspace)/'artifacts'/name for name in ('pair-manifest.json',*FILES)}
  if len(artifacts)!=7 or set(map(Path,artifacts))!=expected:raise ValueError('PAIR_ARTIFACT_PATHS')
  self._intent(claim,workspace,packet)
  self._submit(claim,workspace,packet)
  return self.verified(claim,packet,self.client.read(claim))
 def verified(self,claim,packet,value):
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'packetHash':digest(packet),'verified':True,'sourceApplied':True,'publicationScope':SCOPE}
  if any(value.get(k)!=v for k,v in expected.items()) or not isinstance(value.get('revision'),str) or not value['revision']:raise ValueError('PAIR_RECEIPT_SCOPE')
  refs=value.get('assetRefs');parent=value.get('parentReceipt',{})
  if not isinstance(refs,list) or len(refs)!=2 or refs!=[parent.get('portraitAssetId'),parent.get('pixelAssetId')] or str(parent.get('revision'))!=value['revision']:raise ValueError('PAIR_RECEIPT_ASSETS')
  receipt=Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(refs),value['revision'],True);receipt.validate(claim);return receipt
 def recover(self,claim):
  root=self.folder(claim)
  if not root.exists():return None
  packet=self.packet(claim,root)
  intent=root/'publication-intent.json'
  if not intent.exists():return None
  if digest(json.loads(safe_file(root,intent.name).read_text()))!=digest(packet):raise ValueError('PAIR_INTENT_CHANGED')
  return self.verified(claim,packet,self.client.read(claim))
 def resume_publication(self,claim,expected_publication_hash):
  # Service-only, after a reservation and stopped-execution proof; no generation.
  root=self.folder(claim);packet=self.packet(claim,root)
  if digest(json.loads(safe_file(root,'publication-intent.json').read_text()))!=digest(packet) or digest(packet)!=expected_publication_hash:raise ValueError('PAIR_RESUME_HASH_CHANGED')
  self._submit(claim,root,packet)
  return self.recover(claim)
