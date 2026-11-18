"""Formal-claim Wan adapter. Service resident; engine lifetime fenced per job.

The global execution grant does not authorize idle GPU residency. Consequently
without a separate model lease, owned inference children exit before return.
With that lease, an idle engine may outlive a job; unload confirms its stop.
"""
import hashlib,json,os,re,signal,stat,subprocess,time,urllib.request,tempfile,shutil
from pathlib import Path
from .animation_wan import run
from ..contracts import PROFILES, ExecutionStopUnconfirmed


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def binding(path):
 path=Path(path)
 def checked(p,directory=False):
  p=Path(p);s=p.stat()
  if not p.is_absolute() or p.is_symlink() or s.st_uid!=os.getuid() or s.st_mode&0o022 or (not p.is_dir()if directory else not p.is_file()):raise ValueError('ANIMATION_BINDING_DENIED')
  return p
 checked(path);value=json.loads(path.read_text())
 if set(value)!={'schemaVersion','comfyRoot','python'} or value['schemaVersion']!='ngs.animation-runner-binding.v1':raise ValueError('ANIMATION_BINDING_DENIED')
 root=checked(value['comfyRoot'],True);python=Path(value['python'])
 # Installed Python may be an owner-selected symlink; resolve before checking.
 checked(python.resolve())
 if not python.is_absolute()or not os.access(python,os.X_OK)or not(root/'main.py').is_file():raise ValueError('ANIMATION_BINDING_DENIED')
 assets=Path(__file__).with_name('animation_assets')
 return {'comfyRoot':root,'python':python,'bootstrap':assets/'bootstrap.py','base':assets/'sample.api.json'}

class OwnedComfy:
 def __init__(self,value,root):
  self.binding=value;self.root=Path(root);self.process=None;self.log=None
  self.input_root=self.root/'engine-input';self.output_root=self.root/'engine-output'
  self.url='http://127.0.0.1:8192'
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*a,**k):raise ValueError('ANIMATION_REDIRECT_DENIED')
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def ensure_ready(self,cancel):
  if self.process is not None: return
  if cancel.is_set():raise ValueError('ANIMATION_CANCELLED')
  # Never attach to another process which happens to own this port.
  try:self.opener.open(self.url+'/queue',timeout=2).close()
  except OSError:pass
  else:raise ValueError('ANIMATION_COMFY_PORT_BUSY')
  for p in (self.input_root,self.output_root,self.root/'engine-temp'):p.mkdir(parents=True,exist_ok=True)
  self.log=(self.root/'server.log').open('ab')
  args=[str(self.binding['python']),'-B',str(self.binding['bootstrap']),'--listen','127.0.0.1','--port','8192','--disable-all-custom-nodes','--offline','--reserve-vram','20','--cache-none','--preview-method','none','--bf16-unet','--input-directory',str(self.input_root),'--output-directory',str(self.output_root),'--temp-directory',str(self.root/'engine-temp')]
  try:self.process=subprocess.Popen(args,cwd=self.binding['comfyRoot'],env={**os.environ,'NGS_WAN_COMFY_ROOT':str(self.binding['comfyRoot']),'NGS_WAN_EMBEDDINGS_ROOT':str(self.root/'embeddings')},stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
  except BaseException:self.log.close();self.log=None;raise
 def checked_output(self,item):
  p=self.output_root/item['subfolder']/item['filename'];resolved=p.resolve(strict=True)
  if p.is_symlink()or not resolved.is_relative_to(self.output_root.resolve())or not resolved.is_file():raise ValueError('ANIMATION_OUTPUT_PATH_DENIED')
  return resolved
 def encode(self,args,cancel):
  process=subprocess.Popen(args,start_new_session=True)
  try:
   while process.poll() is None:
    if cancel.wait(.2):raise ValueError('ANIMATION_CANCELLED')
   if process.returncode:raise ValueError('ANIMATION_ENCODE_FAILED')
  finally:self._stop(process)
 @staticmethod
 def _stop(process):
  try:
   if process.poll() is None:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=15)
   else:process.wait()
   # A wrapper may exit while its children survive; never release that slot.
   try:os.killpg(process.pid,signal.SIGKILL)
   except ProcessLookupError:return
   for _ in range(50):
    try:os.killpg(process.pid,0)
    except ProcessLookupError:return
    time.sleep(.1)
   raise ExecutionStopUnconfirmed('ANIMATION_PROCESS_GROUP_ALIVE')
  except ExecutionStopUnconfirmed:raise
  except BaseException as exc:raise ExecutionStopUnconfirmed('ANIMATION_PROCESS_STOP_UNCONFIRMED')from exc
 def unload(self):
  if self.process is not None:self._stop(self.process)
  self.process=None
  if self.log is not None:self.log.close();self.log=None

class AnimationBackend:
 def __init__(self,config,*,engine=run,session_factory=OwnedComfy,residency_root=None):
  self.config=config;self.engine=engine;self.session_factory=session_factory;self.loaded=None;self.session=None
  self.model_lease=None;self.session_root=None;self.residency_root=Path(residency_root)if residency_root else None
  if self.residency_root is not None and (not self.residency_root.is_absolute()or self.residency_root.resolve(strict=True)!=self.residency_root or not self.residency_root.is_dir()):raise ValueError("ANIMATION_RESIDENCY_ROOT_INVALID")
 def set_model_lease(self,lease):
  if self.session is not None or self.model_lease is not None:raise ValueError("ANIMATION_RESIDENCY_ALREADY_BOUND")
  if self.residency_root is None or lease.role!="animation" or not lease.grant_id or not lease.token:raise ValueError("ANIMATION_RESIDENCY_LEASE_REQUIRED")
  self.model_lease=lease
 def load(self):
  # Dependency validation only: model process starts after inputs are verified.
  self.loaded=binding(self.config)
 def unload(self):
  if self.session:self.session.unload()
  self.session=None;self.loaded=None
  if self.session_root is not None:
   shutil.rmtree(self.session_root);self.session_root=None
  self.model_lease=None
 def execute(self,claim,workspace,cancel):
  claim.validate(PROFILES['animation'])
  if self.loaded is None:raise ValueError('ANIMATION_BACKEND_NOT_LOADED')
  root=Path(workspace).resolve(strict=True);path=root/'animation-job.json'
  if path.is_symlink():raise ValueError('ANIMATION_INPUT_BINDING')
  value=json.loads(path.read_text())
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'expectedRevision':claim.expected_revision,'parametersHash':digest(dict(claim.parameters)),'inputRefs':list(claim.input_refs)}
  if set(value)!={'claim','job','inputs'}or value['claim']!=expected:raise ValueError('ANIMATION_INPUT_BINDING')
  job=dict(value['job']);required={'frameCount','fps','appearancePrompt','negativePrompt','motionPrompt','referenceType','framingPolicy'}
  if set(job)!=required or type(job['frameCount'])is not int or not 2<=job['frameCount']<=1000 or type(job['fps'])not in(int,float)or not 0<job['fps']<=120 or job['referenceType']not in ('pixel','portrait')or any(not isinstance(job[k],str)or not 1<=len(job[k])<=12000 for k in ('appearancePrompt','negativePrompt','motionPrompt')):raise ValueError('ANIMATION_JOB_INVALID')
  if set(value['inputs'])!={'reference','motion'}:raise ValueError('ANIMATION_INPUT_BINDING')
  for key,field in [('reference','referencePath'),('motion','motionPath')]:
   item=value['inputs'][key]
   if set(item)!={'path','sha256','ref'}or item['ref']not in claim.input_refs:raise ValueError('ANIMATION_INPUT_BINDING')
   p=root/item['path'];resolved=p.resolve(strict=True)
   if Path(item['path']).is_absolute()or p.is_symlink()or not resolved.is_relative_to(root)or not resolved.is_file()or hashlib.sha256(resolved.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('ANIMATION_INPUT_HASH')
   job[field]=str(resolved)
  job['requestId']='resident.'+digest([claim.request_id,claim.attempt_id])[:48]
  output=root/'wan';output.mkdir(exist_ok=True)
  if cancel.is_set():raise ValueError('ANIMATION_CANCELLED')
  retained=self.model_lease is not None
  if self.session is None:
   if retained:
    self.session_root=Path(tempfile.mkdtemp(prefix="animation-residency-",dir=self.residency_root))
   self.session=self.session_factory(self.loaded,self.session_root if retained else output)
  succeeded=False
  try:
   receipt=self.engine(output,job,self.session,cancel)
   result=Path(receipt['path']).resolve(strict=True)
   if not result.is_relative_to(output)or not result.is_file()or hashlib.sha256(result.read_bytes()).hexdigest()!=receipt['sha256']or receipt.get('frameCount')!=job['frameCount']or receipt.get('fps')!=job['fps']:raise ValueError('ANIMATION_OUTPUT_INVALID')
   succeeded=True
   return (result,)
  finally:
   # Global video slot is released by common runtime only after this join.
   if not retained or not succeeded:
    self.session.unload();self.session=None
