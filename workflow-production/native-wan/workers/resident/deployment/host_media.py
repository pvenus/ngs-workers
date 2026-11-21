"""Read-only trusted host binding. No activation, credentials output or inference.

Selected operator documents are inputs to deployment code, never request DTOs.
Old HQ release pins must not be silently repointed to the resident release.
"""
import hashlib,json,os,re,stat
from pathlib import Path
from .image_music import image_backend,pinned_native_runner,music_backend
from ..backends.frame_full import FullFrameBackend

def trusted_json(path):
 path=Path(path)
 if not path.is_absolute()or path.resolve(strict=True)!=path:raise ValueError('MEDIA_HOST_CONFIG_CANONICAL_REQUIRED')
 before=path.stat()
 if not stat.S_ISREG(before.st_mode)or before.st_uid!=os.getuid()or before.st_mode&0o022 or before.st_size>1024*1024:raise ValueError('MEDIA_HOST_CONFIG_UNTRUSTED')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  with os.fdopen(fd,'rb',closefd=False)as stream:raw=stream.read(1024*1024+1)
  after=os.fstat(fd)
 finally:os.close(fd)
 if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)or len(raw)!=before.st_size:raise ValueError('MEDIA_HOST_CONFIG_CHANGED')
 return json.loads(raw)

def file_hash(path):
 h=hashlib.sha256()
 with Path(path).open('rb')as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def verify_model(row):
 root=Path(row['path'])
 if not root.is_absolute()or not root.is_dir()or root.is_symlink():raise ValueError('FRAME_MODEL_SNAPSHOT_INVALID')
 for name,sha in row['files'].items():
  if Path(name).name!=name or not re.fullmatch('[a-f0-9]{64}',sha)or file_hash(root/name)!=sha:raise ValueError('FRAME_MODEL_HASH_MISMATCH')
 return root

def frame_from_host(adapter_config,verified_hashes,*,ffmpeg,ffprobe,timeout=1200):
 config=trusted_json(adapter_config);pins=trusted_json(verified_hashes)
 keys={'FRAME_ALPHA_PYTHON','FRAME_ALPHA_MODULE_ROOT','FRAME_ALPHA_MATTING_MODEL','FRAME_ALPHA_TRACKING_MODEL'}
 if set(config)!=keys:raise ValueError('FRAME_HOST_CONFIG_SCHEMA')
 matting=verify_model(pins['ZhengPeng7/BiRefNet-matting']);tracking=verify_model(pins['facebook/sam2.1-hiera-small'])
 if Path(config['FRAME_ALPHA_MATTING_MODEL'])!=matting or Path(config['FRAME_ALPHA_TRACKING_MODEL']).parent!=tracking or Path(config['FRAME_ALPHA_TRACKING_MODEL']).name not in pins['facebook/sam2.1-hiera-small']['files']:raise ValueError('FRAME_HOST_MODEL_BINDING')
 return FullFrameBackend(python=config['FRAME_ALPHA_PYTHON'],ffmpeg=ffmpeg,ffprobe=ffprobe,module_root=config['FRAME_ALPHA_MODULE_ROOT'],matting_model=matting,tracking_model=config['FRAME_ALPHA_TRACKING_MODEL'],timeout=timeout)

def image_from_host(host_config,*,binary,runner_type):
 config=trusted_json(host_config)
 if config.get('enabled')is not True or not isinstance(config.get('nativeOutputRoots'),dict):raise ValueError('IMAGE_HOST_CONFIG_DISABLED')
 return image_backend(binary=binary,runner_builder=pinned_native_runner(runner_type,approved_output_roots=config['nativeOutputRoots'].values()))

def music_from_host(config_path,*,lock_fds,lock_validator,enabled):
 config=trusted_json(config_path);release=Path(__file__).resolve().parents[3]
 if config.get('schema')!='private.music-hq-boundary.v1'or Path(config.get('release_root','')).resolve(strict=True)!=release:raise ValueError('MUSIC_NEW_RELEASE_APPROVED_PINS_REQUIRED')
 pins=config.get('file_pins',{})
 required=[release/'workers/audio/hq'/name for name in ('hq_backend.py','hq_contract.py','hq_pins.json','ace_hq_runner.py')]
 if len(pins)!=13 or any(str(path)not in pins for path in required):raise ValueError('MUSIC_HOST_CODE_PIN_SET')
 for name,sha in pins.items():
  if not re.fullmatch('[a-f0-9]{64}',sha)or file_hash(name)!=sha:raise ValueError('MUSIC_HOST_CODE_PIN_CHANGED')
 if Path(config['runner_path'])!=release/'workers/audio/hq/ace_hq_runner.py':raise ValueError('MUSIC_HOST_RUNNER_CHANGED')
 return music_backend(runtime_root=config['runtime_root'],runner_path=config['runner_path'],python_path=config['python_path'],lock_fds=lock_fds,lock_validator=lock_validator,enabled=enabled)
