"""Music provider readiness/binding only; no queue, service or candidate generation."""
import hashlib,json,os,sys
from pathlib import Path
HQ_PROFILE='acestep-xl-sft-lm4b'
TURBO_PROFILE='acestep-v15-turbo'
ACE=Path.home()/'Workspaces/Workers/runtime/acestep/source'
HQ_ACE=Path.home()/'Workspaces/Workers/runtime/acestep-hq/source'
WORKERS_ROOT=Path.home()/'Workspaces/Workers'
def music_boundary(profile=None):
 # This public path remains fixed and owner-private.
 binding=WORKERS_ROOT/('config/ngs-music-hq.private.json'if profile==HQ_PROFILE else'config/ngs-music.private.json')
 return _music_boundary_from_binding(profile,binding)

def _music_boundary_from_binding(profile,binding):
 hq=profile==HQ_PROFILE
 ace=HQ_ACE if hq else ACE
 if profile not in(None,HQ_PROFILE,TURBO_PROFILE):raise ValueError('AUDIO_MUSIC_PROFILE')
 # Fixed owner-private binding. No environment/request-selected executable code.
 import importlib.util,stat
 root=WORKERS_ROOT
 def exact(path):
  path=Path(path)
  if not path.is_absolute()or path.resolve(strict=True)!=path:raise ValueError('AUDIO_WORKERS_PATH')
  return path
 def trusted(path):
  path=exact(path)
  for part in [path,*path.parents]:
   st=part.stat()
   if st.st_uid not in(0,os.getuid())or st.st_mode&0o022:raise ValueError('AUDIO_WORKERS_UNTRUSTED_PATH')
  return path
 def read_verified(path,limit):
  path=trusted(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
  try:
   before=os.fstat(fd)
   if not stat.S_ISREG(before.st_mode)or before.st_size>limit:raise ValueError('AUDIO_WORKERS_FILE_SIZE')
   data=b''
   while len(data)<=limit:
    chunk=os.read(fd,min(65536,limit+1-len(data)))
    if not chunk:break
    data+=chunk
   after=os.fstat(fd);observed=path.lstat()
   identity=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
   if len(data)>limit or identity(before)!=identity(after)or identity(after)!=identity(observed):raise ValueError('AUDIO_WORKERS_CHANGED_READ')
   return data,before
  finally:os.close(fd)
 def unique(pairs):
  out={}
  for key,value in pairs:
   if key in out:raise ValueError('AUDIO_WORKERS_DUPLICATE_KEY')
   out[key]=value
  return out
 raw,st=read_verified(binding,65536)
 if not stat.S_ISREG(st.st_mode)or st.st_uid!=os.getuid()or stat.S_IMODE(st.st_mode)!=0o600:raise ValueError('AUDIO_WORKERS_BINDING_MODE')
 cfg=json.loads(raw,object_pairs_hook=unique)
 if set(cfg)!={'schema','release_root','runtime_root','artifact_root','runner_path','python_path','shared_lock','legacy_lock','directory_pins','file_pins'}or cfg['schema']!=('private.music-hq-boundary.v1'if hq else'private.music-boundary.v1'):raise ValueError('AUDIO_WORKERS_BINDING_SCHEMA')
 if exact(cfg['runtime_root'])!=ace.parent.resolve(strict=True):raise ValueError('AUDIO_WORKERS_LEGACY_SEMANTICS')
 if Path(cfg['python_path'])!=ace/'.venv/bin/python':raise ValueError('AUDIO_WORKERS_INTERPRETER_LAUNCHER')
 release=trusted(cfg['release_root'])
 package=release/'modules/audioforge/audioforge'
 expected_runner=package/'ace_hq_runner.py' if hq else release/'workers/audio/legacy_runner.py'
 if exact(cfg['runner_path'])!=expected_runner:raise ValueError('AUDIO_WORKERS_RUNNER_SCOPE')
 runner_bytes,_=read_verified(cfg['runner_path'],1048576)
 if hashlib.sha256(runner_bytes).hexdigest()!=cfg['file_pins'].get(cfg['runner_path']):raise ValueError('AUDIO_WORKERS_RUNNER_PIN')
 if release.parent!=exact(root/'code/releases'):raise ValueError('AUDIO_WORKERS_RELEASE_SCOPE')
 paths=[release/'workers/__init__.py',release/'workers/audio/__init__.py',release/'workers/audio/boundary.py',release/'workers/audio/legacy_backend.py']
 if hq:paths.append(release/'workers/audio/hq_bridge.py')
 extra=[package/n for n in ('__init__.py','backends.py','hq_contract.py','hq_backend.py','hq_pins.json')]if hq else[]
 if hq:
  required={str(x)for x in paths+extra+[Path(cfg['runner_path']),Path(cfg['python_path']).resolve(strict=True),ace/'.venv/pyvenv.cfg']}
  if set(cfg['file_pins'])!=required:raise ValueError('AUDIO_WORKERS_CODE_PIN_SET')
 hashes=[hashlib.sha256(runner_bytes).hexdigest()]if hq else[];sources={}
 for path in paths+extra:
  data,_=read_verified(path,1048576);h=hashlib.sha256(data).hexdigest()
  if cfg['file_pins'].get(str(path))!=h:raise ValueError('AUDIO_WORKERS_CODE_PIN')
  hashes.append(h);sources[str(path)]=data
 namespace='_ngs_workers_'+hashlib.sha256((''.join(hashes)+str(release)+(json.dumps(cfg['file_pins'],sort_keys=True)if hq else'')).encode()).hexdigest()
 def load(name,path,package=False):
  if name in sys.modules:
   if Path(sys.modules[name].__file__).resolve()!=path:raise ValueError('AUDIO_WORKERS_MODULE_COLLISION')
   return sys.modules[name]
  spec=importlib.util.spec_from_file_location(name,path,submodule_search_locations=[str(path.parent)]if package else None)
  mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod
  try:exec(compile(sources[str(path)],str(path),'exec'),mod.__dict__)
  except BaseException:sys.modules.pop(name,None);raise
  return mod
 load(namespace,paths[0],True);load(namespace+'.audio',paths[1],True)
 module=load(namespace+'.audio.boundary',paths[2]);load(namespace+'.audio.legacy_backend',paths[3])
 if hq:load(namespace+'.audio.hq_bridge',paths[4])
 # Validate runtime/interpreter/runner/module pins and existing common lock before intent.
 module.validate_config(cfg,Path(cfg['artifact_root']))
 fd=module.open_existing_lock(cfg['shared_lock']);os.close(fd)
 if hq and (not callable(getattr(module,'hq_available',None))or module.hq_available(cfg)is not True):raise ValueError('AUDIO_HQ_UNAVAILABLE')
 return module,cfg
