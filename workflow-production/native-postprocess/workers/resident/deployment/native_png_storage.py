"""Opt-in local authority caller; no HTTP route, token export or default wiring.

The authority and operation factory are server composition inputs, never worker
payload. Filex retains the original generation fence and atomic commit lock.
A post-operation stale check may reject a response after a successful commit;
that is uncertain delivery, not a rollback or permission to regenerate.
"""
from ..adapters.common_authority import CommonAuthorityBridge

_PHASES = {
 'ngs.animation-native-sequence-direction.v1': ('animation.generate','animation-sequences',True),
 'ngs.animation-alpha-dependency.v1': ('image.process','animation-alpha',True),
 'ngs.animation-native-sequence-pixel-job.v1': ('frames.extract','animation-processed',False),
}

class NativePNGStorage:
 def __init__(self,authority,operation_factory,*,enabled=False):
  if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
  if not isinstance(authority,CommonAuthorityBridge) or not callable(operation_factory):
   raise ValueError('NATIVE_PNG_STORAGE_AUTHORITY_REQUIRED')
  self.authority=authority;self.factory=operation_factory;self.enabled=enabled

 def _scope(self,claim):
  if not self.enabled or not self.authority.native_png:raise ValueError('NATIVE_PNG_STORAGE_DISABLED')
  p=claim.parameters;phase=_PHASES.get(p.get('schemaVersion'))
  if phase is None or claim.capability!=phase[0] or p.get('requestId')!=claim.request_id:
   raise ValueError('NATIVE_PNG_STORAGE_SCOPE')
  # Exact issued claim and persisted target binding are revalidated by the
  # existing authority before and after its original parent resolver.
  self.authority.authorize_publication(claim)
  guard=self.authority.ownership_guard(claim)
  if (guard['npcId']!=p.get('npcId') or guard['generationId']!=p.get('generationId')
      or guard['requestId']!=p.get('parentRequestId')):
   raise ValueError('NATIVE_PNG_STORAGE_SCOPE')
  path='canonical-files/binari/npcs/{}/{}/{}/{}'.format(p['npcId'],phase[1],p['posePreset'],p['presetId'])
  if phase[2]:path+='/'+p['direction']
  return guard,path+'.json'

 def _call(self,claim,method,*args):
  guard,_=self._scope(claim)
  operation=self.factory(guard,claim.request_id)
  result=getattr(operation,method)(*args)
  self.authority.authorize_publication(claim)
  return result

 def upload_asset(self,claim,asset_id,data):
  return self._call(claim,'upload_asset',asset_id,data)

 def commit(self,claim,input_hash,files,read_set):
  guard,path=self._scope(claim)
  if (input_hash!=claim.expected_revision or type(files)is not list or len(files)!=1
      or files[0].get('path')!=path or type(read_set)is not dict or set(read_set)!={path}):
   raise ValueError('NATIVE_PNG_STORAGE_COMMIT_SCOPE')
  result=self.factory(guard,claim.request_id).commit(claim.request_id,input_hash,files,read_set)
  self.authority.authorize_publication(claim)
  return result

 def receipt(self,claim):
  return self._call(claim,'receipt',claim.request_id)
