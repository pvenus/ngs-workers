"""Local installed role observation for explicitly enabled native PNG coverage.

Uses existing issued claims, source pins and attempt-directory read lock. No
workspace discovery, creation, external scan, or assertion about another host.
"""
import hashlib,json
from dataclasses import asdict
from pathlib import Path
from ..contracts import Claim,PROFILES
from ..terminal_evidence import SourcePin
from ..adapters.common_authority import CommonAuthorityBridge
from ..adapters.attempt_directory_lock import directory_lock
from .request_selector import digest

class NativePNGHostObserver:
 def __init__(self,authority,*,routes,producer_registry_hash,source_pins,verify_cleaned,enabled=False):
  if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
  if not isinstance(authority,CommonAuthorityBridge)or set(routes)!={'animation','postprocess'} or not callable(verify_cleaned):raise ValueError('NATIVE_HOST_CONFIG')
  if not source_pins or any(not isinstance(p,SourcePin)for p in source_pins):raise ValueError('NATIVE_HOST_SOURCE_REQUIRED')
  self.authority=authority;self.routes={k:dict(v)for k,v in routes.items()};self.registry=producer_registry_hash
  self.pins=tuple(source_pins);self.cleaned=verify_cleaned;self.enabled=enabled
  self._check()
 def _check(self):
  for pin in self.pins:
   p=Path(pin.path)
   if not p.is_absolute()or p.is_symlink()or hashlib.sha256(p.read_bytes()).hexdigest()!=pin.sha256:raise ValueError('NATIVE_HOST_SOURCE_CHANGED')
  for route in self.routes.values():
   if set(route)!={'hostId','rootKey','scratchRoot'}or not route['hostId']or not route['rootKey']:raise ValueError('NATIVE_HOST_CONFIG')
   p=Path(route['scratchRoot'])
   if not p.is_absolute()or p.resolve(strict=True)!=p or not p.is_dir():raise ValueError('NATIVE_HOST_ROOT')
 def __call__(self,guard,role,scope_hash,issued_claims):
  if not self.enabled or not self.authority.native_png:raise ValueError('NATIVE_HOST_DISABLED')
  self._check()
  if role not in self.routes:raise ValueError('NATIVE_HOST_ROLE')
  route=self.routes[role];entries=[]
  for value in issued_claims:
   value=dict(value);value['input_refs']=tuple(value['input_refs']);claim=Claim(**value);claim.validate(PROFILES[role]);p=claim.parameters
   schemas={'animation':{'ngs.animation-native-sequence-direction.v1'},'postprocess':{'ngs.animation-alpha-dependency.v1','ngs.animation-native-sequence-pixel-job.v1'}}
   if p.get('schemaVersion')not in schemas[role]or any(p.get(k)!=guard[k]for k in ('npcId','generationId'))or p.get('parentRequestId')!=guard['requestId']:raise ValueError('NATIVE_HOST_SCOPE')
   with self.authority.transaction():
    self.authority._issued_lease(claim,allow_released=True)
    row=self.authority.common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?',(claim.resource_grant,)).fetchone()
    released=bool(row['released'])
   path=Path(route['scratchRoot'])/digest([claim.request_id,claim.attempt_id])
   with directory_lock(path)as exists:
    if exists:
     marker=path/'claim.json'
     if marker.is_symlink()or not marker.is_file()or json.loads(marker.read_text())!=json.loads(json.dumps(asdict(claim))):raise ValueError('NATIVE_HOST_WORKSPACE_OWNER')
    elif not released or self.cleaned(claim)is not True:raise ValueError('NATIVE_HOST_CLEANUP_REQUIRED')
   entries.append(dict(requestId=claim.request_id,attemptId=claim.attempt_id,resourceGrant=claim.resource_grant,path=str(path),authorityHash=digest(asdict(claim))))
  if len({e['resourceGrant']for e in entries})!=len(entries):raise ValueError('NATIVE_HOST_DUPLICATE')
  self._check()
  return dict(hostId=route['hostId'],producerRegistryHash=self.registry,role=role,producer='animation'if role=='animation'else'frames',rootKey=route['rootKey'],kind='animation-job'if role=='animation'else'frame-job',scopeHash=scope_hash,verified=True,entries=entries)
