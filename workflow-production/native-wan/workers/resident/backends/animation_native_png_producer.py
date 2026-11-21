"""Opt-in producer boundary after native SaveImage decode, before MP4 encoding.

Not registered in any production role/route. The caller owns decode and its
request workspace; this adapter runs no model, ffmpeg, PDS or alpha conversion.
Existing MP4 runner paths are deliberately unchanged.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from .animation_native_png import build_native_png_manifest,validate_native_png_manifest

CAPABILITY='animation.generate-native-png'
OUTPUT_MODE='native-png-sequence.v1'

def digest(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def checked(path):
 path=Path(path)
 if not path.is_absolute() or path.is_symlink() or any(p.is_symlink()for p in path.parents)or path.resolve()!=path:
  raise ValueError('NATIVE_PNG_PATH_INVALID')
 return path

@dataclass(frozen=True)
class NativePNGBundle:
 # Paths are private producer-to-uploader handles, never a wire receipt.
 manifest: dict
 manifest_path: Path
 artifacts: tuple[Path,...]
 sequence_hash: str

 def shared_manifest(self):
  """Portable JSON only; PDS resolves assetIds on another computer."""
  return json.loads(json.dumps(self.manifest))

def produce_native_png_bundle(*,capability,output_mode,binding,expected_binding,
 source_binding,read_current_source_binding,decode_root,output_root,frame_count,fps):
 """Read existing frames/00000.png.. in order, preserving each byte verbatim.

expected_binding and source_binding come from the accepted immutable request,
not decode metadata. The read-only callback rechecks source pins before and
following collection. Manifest publication is a later service responsibility.
Outputs use exclusive creation and never overwrite another attempt's files.
No producer byte ceiling is imposed. Transport limits and chunking belong to
the existing storage uploader, not this decode-to-artifact adapter.
 """
 if capability!=CAPABILITY or output_mode!=OUTPUT_MODE:raise ValueError('NATIVE_PNG_EXPLICIT_OPT_IN_REQUIRED')
 if not callable(read_current_source_binding):raise ValueError('NATIVE_PNG_SOURCE_READER_REQUIRED')
 if binding!=expected_binding or digest(source_binding)!=binding.get('sourceBindingHash'):
  raise ValueError('NATIVE_PNG_SOURCE_BINDING_CHANGED')
 if read_current_source_binding()!=source_binding:raise ValueError('NATIVE_PNG_SOURCE_BINDING_CHANGED')
 source=checked(decode_root);target=checked(output_root)
 if not source.is_dir()or target==source or source in target.parents or target in source.parents:
  raise ValueError('NATIVE_PNG_OUTPUT_SCOPE_INVALID')
 if type(frame_count)is not int or not 2<=frame_count<=1000:raise ValueError('NATIVE_PNG_COUNT_INVALID')
 names=[f'{i:05d}.png'for i in range(frame_count)]
 if sorted(p.name for p in source.iterdir())!=names:raise ValueError('NATIVE_PNG_DECODE_SEQUENCE_CHANGED')
 frames=[]
 for name in names:
  p=checked(source/name);stat=p.stat()
  if not p.is_file()or stat.st_size<=0:raise ValueError('NATIVE_PNG_FRAME_SIZE_INVALID')
  raw=p.read_bytes();after=p.stat()
  if len(raw)!=stat.st_size or (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
   raise ValueError('NATIVE_PNG_FRAME_CHANGED_DURING_READ')
  frames.append(raw)
 manifest=build_native_png_manifest(binding,frames,frame_count=frame_count,fps=fps)
 if read_current_source_binding()!=source_binding:raise ValueError('NATIVE_PNG_SOURCE_BINDING_CHANGED')
 if target.exists():raise ValueError('NATIVE_PNG_OUTPUT_ALREADY_EXISTS')
 target.mkdir(mode=0o700,parents=False)
 artifacts=[];seen=set()
 for meta,raw in zip(manifest['frames'],frames):
  if meta['assetId']in seen:continue
  seen.add(meta['assetId']);p=target/meta['assetId']
  with p.open('xb')as f:f.write(raw)
  artifacts.append(p)
 # Re-read actual saved bytes, not just copied metadata. No re-encoding occurs.
 saved=[(target/x['assetId']).read_bytes()for x in manifest['frames']]
 validate_native_png_manifest(manifest,expected_binding,saved,frame_count=frame_count,fps=fps)
 if read_current_source_binding()!=source_binding:raise ValueError('NATIVE_PNG_SOURCE_BINDING_CHANGED')
 final=target/'native-png-manifest.json'
 with final.open('x')as f:json.dump(manifest,f,ensure_ascii=False,sort_keys=True)
 return NativePNGBundle(manifest,final,tuple(artifacts),digest(manifest))
