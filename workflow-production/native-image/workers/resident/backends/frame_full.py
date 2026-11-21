"""Full extraction -> learned alpha -> mandatory .5 pixel processing.

Pinned deployment paths only. All expensive work is in a joined child process;
no direct Filex, no alpha/chroma fallback and no previous-frame-set reuse.
"""
import hashlib,json,os,signal,subprocess,time
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
from ..contracts import ExecutionStopUnconfirmed
from .frame_postprocess import DIRECTIONS
from .image_music_workspace import safe_file,write_new

class FullFrameBackend:
 requires_model=True
 def __init__(self,*,python,ffmpeg,ffprobe,module_root,matting_model,tracking_model,timeout=1200,grace=2):
  self.paths={k:Path(v)for k,v in dict(python=python,ffmpeg=ffmpeg,ffprobe=ffprobe,module_root=module_root,matting_model=matting_model,tracking_model=tracking_model).items()}
  self.timeout,self.grace=timeout,grace;self.lease=None;self._processes=[];self._model_exit={'outcome':'never_started','processGroups':[],'returnCodes':[]}
  self.path_pins={k:self._identity(k,p)for k,p in self.paths.items()}
 def model_unload_evidence(self,lease):
  return deepcopy(getattr(self,'_model_exit',None))
 def set_model_lease(self,lease):
  if lease is not None and (lease.role!='postprocess'or lease.memory_bytes<=0):raise ValueError('FRAME_MODEL_LEASE_INVALID')
  self.lease=lease
 def _identity(self,key,path):
  if not path.is_absolute()or path.parent.resolve(strict=True)!=path.parent:raise ValueError('FRAME_TRUSTED_PATH_REQUIRED')
  resolved=path.resolve(strict=True);st=resolved.stat()
  if key in ('python','ffmpeg','ffprobe')and(not resolved.is_file()or not os.access(path,os.X_OK)):raise ValueError('FRAME_EXECUTABLE_REQUIRED')
  if path.is_symlink()and key not in ('python','ffmpeg','ffprobe','tracking_model'):raise ValueError('FRAME_TRUSTED_PATH_REQUIRED')
  if resolved.is_file():
   h=hashlib.sha256()
   with resolved.open('rb')as stream:
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
   return (str(resolved),st.st_dev,st.st_ino,h.hexdigest())
  return (str(resolved),st.st_dev,st.st_ino)
 def load(self):
  if not 0<self.timeout<=3600:raise ValueError('FRAME_TIMEOUT_INVALID')
  for key,path in self.paths.items():
   if self._identity(key,path)!=self.path_pins[key]:raise ValueError('FRAME_TRUSTED_PATH_CHANGED')
 def unload(self):self.lease=None
 def _stop(self,p):
  for sig in (signal.SIGTERM,signal.SIGKILL):
   try:os.killpg(p.pid,sig)
   except ProcessLookupError:pass
   try:p.wait(timeout=self.grace)
   except subprocess.TimeoutExpired:continue
   end=time.monotonic()+self.grace
   while time.monotonic()<end:
    try:os.killpg(p.pid,0)
    except ProcessLookupError:return
    time.sleep(.02)
  raise ExecutionStopUnconfirmed('FRAME_PROCESS_GROUP_EXIT_UNCONFIRMED')
 def _run(self,source,folder,cancel):
  env=dict(os.environ);env.update(FRAME_ALPHA_PYTHON=str(self.paths['python']),FRAME_FFMPEG=str(self.paths['ffmpeg']),FRAME_FFPROBE=str(self.paths['ffprobe']),FRAME_ALPHA_MODULE_ROOT=str(self.paths['module_root']),FRAME_ALPHA_MATTING_MODEL=str(self.paths['matting_model']),FRAME_ALPHA_TRACKING_MODEL=str(self.paths['tracking_model']),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
  with (folder/'pipeline.log').open('xb')as log:
   self._model_exit=None
   self._execution_proof=None
   p=subprocess.Popen([str(self.paths['python']),str(Path(__file__).with_name('frame_full_alpha.py')),str(source),str(folder)],stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
   try:
    end=time.monotonic()+self.timeout
    while p.poll()is None:
     if cancel.wait(.1)or time.monotonic()>=end:
      raise RuntimeError('FRAME_EXTRACTION_CANCELLED')
    if p.returncode:raise RuntimeError('FRAME_ALPHA_PIPELINE_FAILED')
   finally:
    # Exit proof is independent of success: a failed/cancelled pipeline may
    # release its reservation only after every owned group is confirmed gone.
    self._stop(p)
    self._processes.append((p.pid,p.returncode))
    proof={'kind':'local-processes','processGroups':[row[0]for row in self._processes],'returnCodes':[row[1]for row in self._processes],'outcome':'joined'}
    self._model_exit=deepcopy(proof)
    if getattr(self,'_execution_scope',None)is not None:self._execution_proof=proof
  return json.loads(safe_file(folder,'pipeline-result.json').read_text())
 def execution_evidence(self,claim):
  scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  return deepcopy(getattr(self,'_execution_proof',None))if scope==getattr(self,'_execution_scope',None)else None
 def execute(self,claim,workspace,cancel):
  self._execution_proof=None
  self._processes=[]
  self._execution_scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  if claim.capability!='frames.extract'or claim.parameters.get('scalePreset','full')!='full':raise ValueError('FRAME_FULL_REQUEST_REQUIRED')
  if self.lease is None:raise ValueError('FRAME_MODEL_ADMISSION_REQUIRED')
  root=Path(workspace);staged=json.loads(safe_file(root,'full-frame-inputs.json').read_text())
  if staged.get('requestId')!=claim.request_id or staged.get('attemptId')!=claim.attempt_id or staged.get('expectedRevision')!=claim.expected_revision or set(staged.get('directions',{}))!=set(DIRECTIONS):raise ValueError('FRAME_FULL_INPUT_BINDING')
  if tuple(claim.input_refs)!=tuple('pds:asset:'+staged['directions'][d]['sourceAssetId']for d in DIRECTIONS):raise ValueError('FRAME_VIDEO_INPUT_REFS_CHANGED')
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  pipeline=root/'full-pipeline';pipeline.mkdir(mode=0o700);inputs={}
  for direction,row in staged['directions'].items():
   source=safe_file(root,row['path']);sha=hashlib.sha256(source.read_bytes()).hexdigest()
   if sha!=row['sha256']or row['sourceAssetId']!=sha+'.mp4':raise ValueError('FRAME_VIDEO_HASH_MISMATCH')
   folder=pipeline/direction;folder.mkdir(mode=0o700);result=self._run(source,folder,cancel)
   if result['trace']['sourceSha256']!=sha or result['frameCount']<=0 or len(result['rawFrames'])!=result['frameCount']or len(result['alphaFrames'])!=result['frameCount']:raise ValueError('FRAME_EXTRACTION_RECEIPT_INVALID')
   for field in ('rawFrames','alphaFrames'):
    for name in result[field]:safe_file(folder,str(Path(name).relative_to(folder)))
   inputs[direction]={**result,'sourceAssetId':row['sourceAssetId']}
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  from .frame_processing import process_set
  output=root/'artifacts';output.mkdir(mode=0o700)
  result=process_set(inputs,output,pixel_scale=.5)
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  result.update(requestId=claim.request_id,pipelinePolicy='extract-alpha-pixel-half.v1')
  target=root/'frame-manifest.json';write_new(target,json.dumps(result,sort_keys=True).encode())
  if self._processes:self._execution_proof={'kind':'local-processes','processGroups':[p[0]for p in self._processes],'returnCodes':[p[1]for p in self._processes],'outcome':'joined'}
  self._model_exit=deepcopy(self._execution_proof)
  return (target,*(Path(p)for p in result['artifacts'].values()))
