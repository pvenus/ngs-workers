"""Opt-in learned alpha runner sharing original lease/process teardown semantics."""
import hashlib,json,os
from dataclasses import asdict
from pathlib import Path
from PIL import Image
from .frame_full import FullFrameBackend

class NativeAlphaModel(FullFrameBackend):
 def _command(self,source,folder):
  return [str(self.paths['python']),str(Path(__file__).with_name('frame_native_alpha_cli.py')),str(source),str(folder)]
 def prepare(self,claim,raw_paths,output,cancel):
  if claim.capability!='image.process' or claim.parameters.get('schemaVersion')!='ngs.animation-alpha-dependency.v1':
   raise ValueError('NATIVE_ALPHA_CLAIM_REQUIRED')
  if self.lease is None:raise ValueError('FRAME_MODEL_ADMISSION_REQUIRED')
  self.load()
  self._execution_proof=None;self._processes=[]
  self._execution_scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  output=Path(output)
  if output.is_symlink()or not output.is_dir()or list(output.iterdir()):raise ValueError('NATIVE_ALPHA_OUTPUT_REQUIRED')
  job=output/'model';job.mkdir(mode=0o700);raw=job/'raw';raw.mkdir(mode=0o700)
  raw_paths=tuple(raw_paths)
  hashes=[];sizes=[]
  for i,value in enumerate(raw_paths):
   p=Path(value)
   if p.is_symlink()or not p.is_file():raise ValueError('NATIVE_ALPHA_RAW_REQUIRED')
   data=p.read_bytes()
   with Image.open(p)as im:
    im.load()
    if im.format!='PNG':raise ValueError('NATIVE_ALPHA_RAW_REQUIRED')
    sizes.append(im.size)
   hashes.append(hashlib.sha256(data).hexdigest());(raw/f'frame-{i:08d}.png').write_bytes(data)
  if not hashes or len(set(sizes))!=1:raise ValueError('NATIVE_ALPHA_RAW_REQUIRED')
  sequence=hashlib.sha256(json.dumps(hashes,separators=(',',':')).encode()).hexdigest()
  key=hashlib.sha256(json.dumps(self.path_pins,sort_keys=True).encode()).hexdigest()
  source=job/'native-input.json';source.write_text(json.dumps(dict(schemaVersion='ngs.native-alpha-local-input.v1',rawSha256=hashes,sequenceHash=sequence,modelKey=key)))
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  result=self._run(source,job,cancel)
  if result.get('modelKey')!=key or result.get('sourceSha256')!=sequence or result.get('frameCount')!=len(hashes) or len(result.get('alphaSha256',[]))!=len(hashes):raise ValueError('NATIVE_ALPHA_RESULT_CHANGED')
  if [hashlib.sha256((raw/f'frame-{i:08d}.png').read_bytes()).hexdigest()for i in range(len(hashes))]!=hashes:raise ValueError('NATIVE_ALPHA_INPUT_CHANGED')
  paths=[]
  for i,sha in enumerate(result['alphaSha256']):
   p=job/'alpha'/f'frame-{i:08d}.png'
   if p.is_symlink()or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('NATIVE_ALPHA_RESULT_CHANGED')
   with Image.open(p)as im:
    im.load()
    if im.mode!='RGBA'or im.size!=sizes[i]:raise ValueError('NATIVE_ALPHA_RESULT_CHANGED')
   dest=output/f'{i:08d}.png';dest.write_bytes(p.read_bytes());paths.append(dest)
  if len(paths)!=len(hashes):raise ValueError('NATIVE_ALPHA_RESULT_CHANGED')
  if [hashlib.sha256(Path(p).read_bytes()).hexdigest()for p in raw_paths]!=hashes:raise ValueError('NATIVE_ALPHA_INPUT_CHANGED')
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  return tuple(paths)
