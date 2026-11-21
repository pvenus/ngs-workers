"""Explicit service-only raw/alpha receipt recovery. No upload, commit or model port.

readback is the existing authenticated, original-owner scoped authority reader,
not a worker packet. Original execution-stop evidence remains mandatory. Only
ReconciliationSupervisor owns FINALIZE transitions and resource release.
"""
import hashlib,io,json
from dataclasses import asdict
from PIL import Image
from ..contracts import Receipt
from .common_authority import CommonAuthorityBridge
from .attempt_directory_lock import directory_lock
from .frame_workspace import digest
from .native_png_workspace import RAW,ALPHA

class NativePNGRecoveryReader:
 def __init__(self,authority,workflow,workspace,*,readback,read_asset,enabled=False):
  if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
  if not isinstance(authority,CommonAuthorityBridge)or not callable(readback)or not callable(read_asset):raise ValueError('NATIVE_RECOVERY_CONFIG')
  self.authority=authority;self.workflow=workflow;self.workspace=workspace
  self.readback=readback;self.asset=read_asset;self.enabled=enabled
 def _identity(self,c):
  if not self.enabled or not self.authority.native_png:raise ValueError('NATIVE_RECOVERY_DISABLED')
  if c.parameters.get('schemaVersion')not in (RAW,ALPHA):raise ValueError('NATIVE_RECOVERY_PHASE_UNSUPPORTED')
  with self.authority.transaction():self.authority._issued_lease(c,allow_released=True)
  if self.authority.verify_stopped(c)is not True:raise ValueError('NATIVE_RECOVERY_STOP_UNCONFIRMED')
 def _inspect(self,c):
  self._identity(c);phase='raw'if c.parameters['schemaVersion']==RAW else'alpha'
  folder=self.workspace._folder(c)
  with directory_lock(folder,exclusive=False)as exists:
   if not exists:return None
   def load(name):
    p=folder/name
    if p.is_symlink()or not p.is_file():raise ValueError('NATIVE_RECOVERY_WORKSPACE_INVALID')
    return json.loads(p.read_text())
   owner=load('claim.json')
   if owner!=json.loads(json.dumps(asdict(c))):raise ValueError('NATIVE_RECOVERY_OWNER_CHANGED')
   intent_path=folder/'publication-intent.json'
   if not intent_path.exists()and not intent_path.is_symlink():return None
   intent=load('publication-intent.json')
   stored=self.readback(phase,c.parameters)
   if stored is None:return None # Unknown, never certified absence.
   if type(stored)is not dict or set(stored)!={'manifestId','manifest','receipt'}:raise ValueError('NATIVE_RECOVERY_READBACK_INVALID')
   m=stored['manifest']
   if intent!={'requestId':c.request_id,'manifestHash':digest(m),'path':stored['manifestId']}:raise ValueError('NATIVE_RECOVERY_INTENT_CHANGED')
   key='assetId'if phase=='raw'else'alphaAssetId'
   ids=tuple(f[key]for f in m['frames'])
   r=Receipt(c.request_id,c.attempt_id,c.fencing_token,ids,digest(m),True)
   # Existing workflow validates original plan, exact Filex receipt scope,
   # inputHash, generation/parent/request, timing and complete ordered manifest.
   proof=self.workflow.completion_proof(phase,c.request_id,receipt_asset_ids=r.asset_refs,receipt_revision=r.revision,readback=lambda *_:stored)
   for frame,aid in zip(m['frames'],ids):
    data=self.asset(aid)
    if hashlib.sha256(data).hexdigest()+'.png'!=aid or len(data)!=frame['bytes'] or hashlib.sha256(data).hexdigest()!=frame['sha256']:raise ValueError('NATIVE_RECOVERY_ASSET_CHANGED')
    with Image.open(io.BytesIO(data))as png:
     if png.format!='PNG'or png.size!=(m['width'],m['height']):raise ValueError('NATIVE_RECOVERY_PNG_INVALID')
     png.load()
   if load('claim.json')!=owner or load('publication-intent.json')!=intent:raise ValueError('NATIVE_RECOVERY_WORKSPACE_CHANGED')
   self._identity(c)
   return r,proof
 def __call__(self,c):
  found=self._inspect(c);return found[0]if found else None
 def verify(self,c,receipt):
  found=self._inspect(c)
  if found is None or found[0]!=receipt:raise ValueError('NATIVE_RECOVERY_RECEIPT_CHANGED')
  return found[1]
