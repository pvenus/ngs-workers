"""Formal image claim -> pinned native provider port -> PDS-staged artifacts.

No native tool client is available by default. A trusted deployment must inject a
cancel-aware provider; request parameters can never select modules/commands.
"""
import hashlib,json,struct,zlib
from dataclasses import asdict
from copy import deepcopy
from .image_music_workspace import digest,stage,output_directory,write_new


def validate_png(raw):
 if not isinstance(raw,bytes)or len(raw)>128*1024*1024 or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('IMAGE_PNG_REQUIRED')
 pos=8;parts=[];header=None;ended=False
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('IMAGE_PNG_TRUNCATED')
  size=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8];data=raw[pos+8:pos+8+size]
  if pos+12+size>len(raw)or zlib.crc32(kind+data)&0xffffffff!=struct.unpack('>I',raw[pos+8+size:pos+12+size])[0]:raise ValueError('IMAGE_PNG_CRC')
  if pos==8 and kind!=b'IHDR':raise ValueError('IMAGE_PNG_HEADER')
  if kind==b'IHDR':
   if header is not None or size!=13:raise ValueError('IMAGE_PNG_HEADER')
   header=struct.unpack('>IIBBBBB',data)
  elif kind==b'IDAT':parts.append(data)
  elif kind==b'IEND':
   if size or pos+12!=len(raw):raise ValueError('IMAGE_PNG_END')
   ended=True
  pos+=12+size
 if not ended or not header or not parts:raise ValueError('IMAGE_PNG_INCOMPLETE')
 w,h,depth,color,compression,filtering,interlace=header
 if not 0<w<=16384 or not 0<h<=16384 or depth!=8 or color not in (0,2,4,6)or compression or filtering or interlace:raise ValueError('IMAGE_PNG_FORMAT')
 expected=h*(w*{0:1,2:3,4:2,6:4}[color]+1)
 if expected>128*1024*1024:raise ValueError('IMAGE_PNG_TOO_LARGE')
 decoder=zlib.decompressobj();decoded=decoder.decompress(b''.join(parts),expected+1)
 if len(decoded)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('IMAGE_PNG_DATA')
 stride=expected//h
 if any(decoded[y*stride]>4 for y in range(h)):raise ValueError('IMAGE_PNG_FILTER')


class ImageGenerationBackend:
 """Provider.run(claim, workspace, cancel) must return only after child exit.
 It returns (PNG bytes, verified native proof); never an output file path.
 """
 def __init__(self,provider_factory=None):self.factory=provider_factory;self.loaded=False
 def load(self):
  if not callable(self.factory):raise RuntimeError('IMAGE_NATIVE_PROVIDER_NOT_BOUND')
  self.loaded=True
 def unload(self):self.loaded=False
 def execution_evidence(self,claim):
  scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  return deepcopy(getattr(self,'_execution_proof',None))if scope==getattr(self,'_execution_scope',None)else None
 def execute(self,claim,workspace,cancel):
  self._execution_proof=None
  self._execution_scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  if not self.loaded:self.load() # image profile has no resident model to preload
  if claim.capability not in ('image.generate','image.edit'):raise ValueError('IMAGE_CAPABILITY_REQUIRED')
  root,packet,refs=stage(claim,workspace);native=packet.get('execution',{}).get('providerClaim')
  if not isinstance(native,dict):raise ValueError('IMAGE_PINNED_CLAIM_REQUIRED')
  provider=native.get('providerRequest',{});args=provider.get('arguments',{})
  if provider.get('providerId')!='codex.image_gen'or provider.get('toolName')!='image_gen.imagegen'or native.get('providerRequestHash')!=digest(provider):raise ValueError('IMAGE_PROVIDER_BINDING')
  if set(args)-{'prompt','transparent_background','referenced_image_paths'} or not isinstance(args.get('prompt'),str)or not args['prompt'].strip()or type(args.get('transparent_background'))is not bool:raise ValueError('IMAGE_ARGUMENTS_INVALID')
  if args.get('referenced_image_paths',[])!=[str(p)for p in refs]or claim.capability=='image.edit'and not refs:raise ValueError('IMAGE_REFERENCE_BINDING')
  if not all(native.get(k)for k in ('requestHash','providerClaim'))or type(native.get('index'))is not int:raise ValueError('IMAGE_PINNED_CLAIM_REQUIRED')
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  folder=output_directory(root)
  provider=self.factory(root)
  # A legacy client lacking cancellation/join must not be bound to this port.
  from ..contracts import ExecutionStopUnconfirmed
  try:raw,proof=provider.run(native,root,cancel)
  except ExecutionStopUnconfirmed:raise
  except Exception as error:
   raise ExecutionStopUnconfirmed('IMAGE_PROVIDER_RESULT_UNCONFIRMED')from error
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  if not isinstance(proof,dict)or proof.get('actualToolOutput')is not True or proof.get('sha256')!=hashlib.sha256(raw).hexdigest()or any(proof.get(k)!=native[k]for k in ('index','providerClaim','requestHash','providerRequestHash')):raise ValueError('IMAGE_NATIVE_PROOF_MISMATCH')
  validate_png(raw);image=folder/(proof['sha256']+'.png');write_new(image,raw)
  receipt=folder/'image-result.json'
  write_new(receipt,json.dumps({'requestId':claim.request_id,'attemptId':claim.attempt_id,'provider':'codex.image_gen','sha256':proof['sha256'],'providerRequestHash':native['providerRequestHash'],'actualToolOutput':True},sort_keys=True).encode())
  evidence=getattr(provider,'process_evidence',None)
  if isinstance(evidence,dict)and evidence.get('outcome')=='joined':self._execution_proof=deepcopy(evidence)
  return image,receipt
