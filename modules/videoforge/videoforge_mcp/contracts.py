from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any

MAX_SWAP_BYTES=2*1024**3
MIN_FREE_BYTES=25*1024**3
MAX_TIMEOUT_SECONDS=1800

def candidate_indices(frame_count:int,target_n:int)->list[int]:
 if frame_count<1 or target_n<1: raise ValueError('frame_count and target_n must be positive')
 count=min(frame_count,2*target_n)
 if count==1:return [0]
 result=[round(i*(frame_count-1)/(count-1)) for i in range(count)]
 return list(dict.fromkeys(result))

def sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def atomic_json(path:Path,data:dict[str,Any])->None:
 path.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name+'.')
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
  os.replace(name,path)
 finally:
  Path(name).unlink(missing_ok=True)

def validate_capacity(runtime_root:Path,swap_bytes:int,timeout_seconds:int)->None:
 if shutil.disk_usage(runtime_root).free<MIN_FREE_BYTES:raise RuntimeError('insufficient_disk')
 if swap_bytes>MAX_SWAP_BYTES:raise RuntimeError('swap_limit')
 if not 1<=timeout_seconds<=MAX_TIMEOUT_SECONDS:raise ValueError('timeout_limit')

def selection_manifest(candidate_manifest:Path,selected_ids:list[str],output:Path,manifest_root:Path|None=None,output_root:Path|None=None)->dict[str,Any]:
 from .paths import confined
 if manifest_root is None or output_root is None:raise ValueError('path_roots_required')
 candidate_manifest=confined(candidate_manifest,manifest_root,True);output=confined(output,output_root)
 source=json.loads(candidate_manifest.read_text());known={x['candidate_id'] for x in source['candidates']}
 if len(selected_ids)!=len(set(selected_ids)) or not set(selected_ids)<=known:raise ValueError('invalid_selection')
 data={'schema_version':1,'candidate_manifest_path':str(candidate_manifest),'candidate_manifest_sha256':sha256(candidate_manifest),'selected_candidate_ids':selected_ids,'automatic_final_selection':False}
 atomic_json(output,data);return data
