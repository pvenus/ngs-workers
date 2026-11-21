"""Formal-claim Wan adapter. Service resident; engine lifetime fenced per job.

The global execution grant does not authorize idle GPU residency. Consequently
without a separate model lease, owned inference children exit before return.
With that lease, an idle engine may outlive a job; unload confirms its stop.
"""
import copy,hashlib,json,os,re,signal,stat,subprocess,time,urllib.request,tempfile,shutil
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
  self.binding=value;self.root=Path(root);self.process=None;self.log=None;self.process_identity=None;self.stopped_groups=[];self.terminal=None;self.active_prompt=None
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
  import psutil
  self.process_identity={"pid":self.process.pid,"pgid":os.getpgid(self.process.pid),"createTime":psutil.Process(self.process.pid).create_time()}
 def checked_output(self,item):
  p=self.output_root/item['subfolder']/item['filename'];resolved=p.resolve(strict=True)
  if p.is_symlink()or not resolved.is_relative_to(self.output_root.resolve())or not resolved.is_file():raise ValueError('ANIMATION_OUTPUT_PATH_DENIED')
  return resolved
 def cleanup_job(self,request_id,artifact_sha):
  # Called only after the service has independently verified this sealed job.
  if (not re.fullmatch(r'resident\.[a-f0-9]{48}',request_id) or self.active_prompt
      or not self.terminal or self.terminal.get('engineRequestId')!=request_id
      or self.terminal.get('artifactSha256')!=artifact_sha
      or self.terminal.get('queue',{}).get('queue_running')!=[]
      or self.terminal.get('queue',{}).get('queue_pending')!=[]):
   raise ValueError('ANIMATION_JOB_CLEANUP_NOT_TERMINAL')
  root=self.root.resolve(strict=True);paths=[]
  for directory in ('engine-input','engine-output','embeddings','engine-temp'):
   path=self.root/directory/'ngs-animation'/request_id
   # Check every existing component before deleting anything. No parent sweep.
   cursor=path
   while cursor!=self.root:
    if cursor.is_symlink():raise ValueError('ANIMATION_JOB_CLEANUP_SYMLINK')
    cursor=cursor.parent
   if path.exists():
    if not path.is_dir() or not path.resolve().is_relative_to(root) or any(p.is_symlink()for p in path.rglob('*')):
     raise ValueError('ANIMATION_JOB_CLEANUP_PATH')
    paths.append(path)
  for path in paths:shutil.rmtree(path)
  return True
 def encode(self,args,cancel):
  process=subprocess.Popen(args,start_new_session=True)
  import psutil
  identity={"pid":process.pid,"pgid":os.getpgid(process.pid),"createTime":psutil.Process(process.pid).create_time()}
  try:
   while process.poll() is None:
    if cancel.wait(.2):raise ValueError('ANIMATION_CANCELLED')
   if process.returncode:raise ValueError('ANIMATION_ENCODE_FAILED')
  finally:
   self._stop(process);self.stopped_groups.append({**identity,"returncode":process.returncode,"waitConfirmed":True})
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
  if self.process is not None:
   self._stop(self.process)
   if self.process_identity:self.stopped_groups.append({**self.process_identity,"returncode":self.process.returncode,"waitConfirmed":True})
  self.process=None
  if self.log is not None:self.log.close();self.log=None

class AnimationBackend:
 def __init__(self,config,*,engine=run,session_factory=OwnedComfy,residency_root=None,keep_model_idle=False,retained_port=None):
  if type(keep_model_idle)is not bool:raise ValueError("ANIMATION_IDLE_POLICY_INVALID")
  self.retained_port=retained_port
  self.keep_model_idle=keep_model_idle;self.release_model_after_execute=not keep_model_idle
  self.config=config;self.engine=engine;self.session_factory=session_factory;self.loaded=None;self.session=None
  self.execution_proofs={};self.unload_proofs={};self.model_groups=[]
  self.preserve_session_files=False
  self.model_lease=None;self.session_root=None;self.residency_root=Path(residency_root)if residency_root else None
  if self.residency_root is not None and (not self.residency_root.is_absolute()or self.residency_root.resolve(strict=True)!=self.residency_root or not self.residency_root.is_dir()):raise ValueError("ANIMATION_RESIDENCY_ROOT_INVALID")
 def set_model_lease(self,lease):
  if lease is None:
   if self.session is not None or self.model_lease is not None:raise ValueError("ANIMATION_RESIDENCY_STILL_BOUND")
   return
  if self.session is not None or self.model_lease is not None:raise ValueError("ANIMATION_RESIDENCY_ALREADY_BOUND")
  if self.residency_root is None or lease.role!="animation" or not lease.grant_id or not lease.token:raise ValueError("ANIMATION_RESIDENCY_LEASE_REQUIRED")
  self.model_groups=[];self.model_lease=lease
 def load(self):
  # Dependency validation only: model process starts after inputs are verified.
  value=binding(self.config)
  if self.engine is run:
   # Validate the native runner dependency before runtime enters execute.
   # A failure here uses the existing never_started receipt, not a guessed join.
   import psutil
  self.loaded=value
 def unload(self):
  lease=self.model_lease
  never_started=self.session is None and not self.model_groups
  if self.session:
   self.session.unload();self.model_groups.extend(getattr(self.session,"stopped_groups",[]))
  if lease is not None and self.model_groups:
   self.unload_proofs[(lease.grant_id,lease.epoch)]={"schemaVersion":"resident.animation-model-unload.v1","grantId":lease.grant_id,"epoch":lease.epoch,"workerId":lease.worker_id,"processGroups":copy.deepcopy(self.model_groups)}
  if lease is not None and never_started:
   self.unload_proofs[(lease.grant_id,lease.epoch)]={"neverStarted":True}
  self.session=None;self.loaded=None
  if self.session_root is not None:
   if not self.preserve_session_files:shutil.rmtree(self.session_root)
   self.session_root=None
  self.model_lease=None;self.preserve_session_files=False
 def execution_evidence(self,claim):
  proof=self.execution_proofs.get((claim.request_id,claim.attempt_id,claim.fencing_token))
  if not proof:return None
  if proof['mode']=='processes-stopped':return self._joined_evidence(proof['processGroups'])
  return {'outcome':'retained_idle','processGroups':[proof['engine']['pgid']],'returnCodes':[],**{k:copy.deepcopy(proof[k])for k in ('engine','terminal','modelGrantId')}}
 @staticmethod
 def _joined_evidence(groups):
  if not groups or any(g.get('waitConfirmed')is not True or type(g.get('returncode'))is not int for g in groups):return None
  # One entry per owned process group, after wait and absence checks.
  unique={g['pgid']:g['returncode']for g in groups}
  return {'outcome':'joined','processGroups':list(unique),'returnCodes':list(unique.values())}
 def model_unload_evidence(self,lease):
  proof=self.unload_proofs.get((lease.grant_id,lease.epoch))
  if proof and proof.get('neverStarted'):return {'outcome':'never_started','processGroups':[],'returnCodes':[]}
  return self._joined_evidence(proof['processGroups'])if proof else None
 def execute(self,claim,workspace,cancel):
  claim.validate(PROFILES['animation'])
  proof_key=(claim.request_id,claim.attempt_id,claim.fencing_token);self.execution_proofs.pop(proof_key,None)
  if self.loaded is None:raise ValueError('ANIMATION_BACKEND_NOT_LOADED')
  root=Path(workspace).resolve(strict=True);path=root/'animation-job.json'
  if path.is_symlink():raise ValueError('ANIMATION_INPUT_BINDING')
  value=json.loads(path.read_text())
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'expectedRevision':claim.expected_revision,'parametersHash':digest(dict(claim.parameters)),'inputRefs':list(claim.input_refs)}
  if set(value)!={'claim','job','inputs'}or value['claim']!=expected:raise ValueError('ANIMATION_INPUT_BINDING')
  job=dict(value['job']);required={'frameCount','fps','appearancePrompt','negativePrompt','motionPrompt','referenceType','framingPolicy'}
  if set(job)-{'seed'}!=required or type(job['frameCount'])is not int or not 2<=job['frameCount']<=1000 or type(job['fps'])not in(int,float)or not 0<job['fps']<=120 or job['referenceType']not in ('pixel','portrait')or any(not isinstance(job[k],str)or not 1<=len(job[k])<=12000 for k in ('appearancePrompt','negativePrompt','motionPrompt')):raise ValueError('ANIMATION_JOB_INVALID')
  if 'seed'in job and(type(job['seed'])is not int or not 0<=job['seed']<=4294967295):raise ValueError('ANIMATION_SEED_INVALID')
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
  if retained and self.keep_model_idle and self.retained_port is None:raise ValueError("ANIMATION_RETAINED_PORT_REQUIRED")
  if self.session is None:
   if retained:
    self.session_root=Path(tempfile.mkdtemp(prefix="animation-residency-",dir=self.residency_root));self.preserve_session_files=False
   self.session=self.session_factory(self.loaded,self.session_root if retained else output)
  if retained and self.keep_model_idle and self.retained_port is not None:
   self.session.register_prompt=lambda prompt:self.retained_port.register(claim,self.model_lease,self.session.process_identity,prompt)
  succeeded=False
  def scratch_status(state,artifact_sha=None):
   if self.session_root is None:return
   path=root/'resident-engine-scratch.json'
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w')as stream:
    json.dump({'schemaVersion':'resident.animation-scratch.v1','engineRequestId':job['requestId'],'sessionRoot':str(self.session_root),'state':state,'artifactSha256':artifact_sha},stream);stream.flush();os.fsync(stream.fileno())
  self.session.terminal=None
  try:
   scratch_status('outcome-unknown')
   receipt=self.engine(output,job,self.session,cancel)
   result=Path(receipt['path']).resolve(strict=True)
   if not result.is_relative_to(output)or not result.is_file()or hashlib.sha256(result.read_bytes()).hexdigest()!=receipt['sha256']or receipt.get('frameCount')!=job['frameCount']or receipt.get('fps')!=job['fps']:raise ValueError('ANIMATION_OUTPUT_INVALID')
   if retained and self.keep_model_idle and self.retained_port is not None:
    # The publication artifact is durable in task scratch before engine copies go.
    with result.open('rb')as stream:os.fsync(stream.fileno())
    completion=output/'completed.json'
    if completion.exists():
     if completion.is_symlink():raise ValueError('ANIMATION_COMPLETION_PATH')
     with completion.open('rb')as stream:os.fsync(stream.fileno())
    fd=os.open(output,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    self.retained_port.seal(claim,self.model_lease,self.session.terminal['promptId'])
    hook={'outcome':'retained_idle','engine':self.session.process_identity,'modelGrantId':self.model_lease.grant_id,'terminal':self.session.terminal}
    self.retained_port.record_execution(claim,self.model_lease.worker_id,hook)
    self.session.cleanup_job(job['requestId'],receipt['sha256'])
    scratch_status('retained-job-cleaned',receipt['sha256'])
   succeeded=True
   return (result,)
  finally:
   # Global video slot is released by common runtime only after this join.
   session=self.session
   proof={'schemaVersion':'resident.animation-execution-evidence.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'resourceGrant':claim.resource_grant}
   if not retained or not self.keep_model_idle or not succeeded:
    if retained and not succeeded:self.preserve_session_files=True
    session.unload();self.session=None
    # A confirmed join before publication makes engine scratch disposable.
    self.preserve_session_files=False
    scratch_status('session-stopped')
    groups=getattr(session,'stopped_groups',[])
    if groups:
     self.model_groups.extend(groups)
     self.execution_proofs[proof_key]={**proof,'mode':'processes-stopped','processGroups':copy.deepcopy(groups)}
   elif getattr(session,'terminal',None) and getattr(session,'process_identity',None) and not getattr(session,'active_prompt',None):
    self.execution_proofs[proof_key]={**proof,'mode':'retained-idle','engine':copy.deepcopy(session.process_identity),'terminal':copy.deepcopy(session.terminal),'processGroups':copy.deepcopy(session.stopped_groups),'modelGrantId':self.model_lease.grant_id}
