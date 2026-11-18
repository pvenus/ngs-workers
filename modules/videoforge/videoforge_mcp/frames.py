from __future__ import annotations
from pathlib import Path
import math
import fcntl
import os
import shutil
import tempfile
from typing import Any
import imageio.v3 as iio
from .contracts import atomic_json,candidate_indices,sha256
from .paths import confined

def extract_candidates(source:Path,output_dir:Path,target_n:int=30,source_root:Path|None=None,output_root:Path|None=None)->dict[str,Any]:
 if source_root is None or output_root is None:raise ValueError('path_roots_required')
 source=confined(source,source_root,True)
 output_dir=confined(output_dir,output_root)
 if output_dir==output_root.resolve():raise ValueError('output_dir_required')
 if output_dir.exists():raise FileExistsError('candidate_output_exists')
 meta=iio.immeta(source);fps=float(meta.get('fps') or 0)
 raw_count=meta.get('nframes')
 frame_count=int(raw_count) if isinstance(raw_count,(int,float)) and math.isfinite(raw_count) else 0
 if frame_count<1:
  frame_count=sum(1 for _ in iio.imiter(source))
 chosen=candidate_indices(frame_count,target_n);wanted=set(chosen)
 output_dir.parent.mkdir(parents=True,exist_ok=True)
 # The lock covers both stale-stage cleanup and publication across MCP processes.
 with (output_dir.parent/'.videoforge-extract.lock').open('a+b') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if output_dir.exists():raise FileExistsError('candidate_output_exists')
  prefix=f'.{output_dir.name}.extract-'
  for stale in output_dir.parent.glob(prefix+'*'):
   if stale.is_dir() and not stale.is_symlink():shutil.rmtree(stale)
  stage=Path(tempfile.mkdtemp(prefix=prefix,dir=output_dir.parent))
  try:
   candidates=[];width=height=None
   for index,frame in enumerate(iio.imiter(source)):
    if index>=frame_count:break
    if index not in wanted:continue
    if width is None:height,width=int(frame.shape[0]),int(frame.shape[1])
    name=f'frame-{index:06d}.png';path=stage/name;iio.imwrite(path,frame)
    candidates.append({'candidate_id':name[:-4],'frame_index':index,'timestamp_seconds':index/fps if fps else None,'path':str(output_dir/name),'sha256':sha256(path)})
   if len(candidates)!=len(chosen):raise RuntimeError('source_frame_count_changed')
   manifest={'schema_version':1,'source':{'path':str(source),'sha256':sha256(source),'fps':fps or None,'frame_count':frame_count,'duration_seconds':frame_count/fps if fps else None,'width':width,'height':height},'target_n':target_n,'candidate_limit':2*target_n,'candidates':candidates,'diagnostics':[]}
   atomic_json(stage/'candidates.json',manifest)
   if output_dir.exists():raise FileExistsError('candidate_output_exists')
   os.rename(stage,output_dir)
   return manifest
  finally:
   if stage.exists():shutil.rmtree(stage)
