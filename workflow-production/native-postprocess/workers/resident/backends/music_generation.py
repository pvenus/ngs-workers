"""Existing HQ music subprocess execution behind the resident worker ports.

The SERVICE stays resident; the current HQ model processes deliberately exit
between LM4B and XL-SFT. This adapter does not claim model-resident inference.
"""
import hashlib,json,wave
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
from .image_music_workspace import stage,output_directory,write_new

class MusicGenerationBackend:
 def __init__(self,engine_factory=None):self.factory=engine_factory;self.loaded=False;self.model_lease=None;self.running=False;self.execution_unknown=False;self._model_exit={'outcome':'never_started','processGroups':[],'returnCodes':[]}
 def model_unload_evidence(self,lease):
  return deepcopy(getattr(self,'_model_exit',None))
 def set_model_lease(self,lease):
  if lease is not None and (getattr(lease,'role',None)!='music'or type(getattr(lease,'memory_bytes',None))is not int or lease.memory_bytes<=0):raise ValueError('MUSIC_MODEL_LEASE_INVALID')
  if lease is None and (self.running or self.execution_unknown):
   from ..contracts import ExecutionStopUnconfirmed
   raise ExecutionStopUnconfirmed('MUSIC_PROCESS_EXIT_UNCONFIRMED')
  self.model_lease=lease
 def load(self):
  if not callable(self.factory):raise RuntimeError('MUSIC_HQ_PROVIDER_NOT_BOUND')
  if self.model_lease is None:raise ValueError('MUSIC_MODEL_ADMISSION_REQUIRED')
  self.loaded=True
 def unload(self):
  if self.running or self.execution_unknown:
   from ..contracts import ExecutionStopUnconfirmed
   raise ExecutionStopUnconfirmed('MUSIC_PROCESS_EXIT_UNCONFIRMED')
  self.loaded=False
 def execution_evidence(self,claim):
  scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  return deepcopy(getattr(self,'_execution_proof',None))if scope==getattr(self,'_execution_scope',None)else None
 def execute(self,claim,workspace,cancel):
  self._execution_proof=None
  self._execution_scope=hashlib.sha256(json.dumps(asdict(claim),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  if not self.loaded:raise RuntimeError('MUSIC_BACKEND_NOT_LOADED')
  if self.model_lease is None:raise ValueError('MUSIC_MODEL_ADMISSION_REQUIRED')
  if self.running or self.execution_unknown:raise RuntimeError('MUSIC_EXECUTION_RECONCILIATION_REQUIRED')
  if claim.capability!='music.generate':raise ValueError('MUSIC_CAPABILITY_REQUIRED')
  root,packet,refs=stage(claim,workspace)
  if refs:raise ValueError('MUSIC_REFERENCE_INPUT_NOT_SUPPORTED')
  # Reuse the existing versioned HQ request, not a replacement worker DTO.
  from workers.audio.hq.hq_contract import validate_payload
  request=dict(claim.parameters);validate_payload(request)
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  folder=output_directory(root);output=folder/'music.wav'
  engine=self.factory(folder)
  # The existing HQBackend observes cancellation, kills/joins each child and
  # validates pinned runtime, inherited locks and ordered LM -> XL receipts.
  from ..contracts import ExecutionStopUnconfirmed
  self.running=True
  self._model_exit=None
  try:
   provenance=engine.generate(request['prompt'],request['duration_seconds'],request.get('seed',0),request['model'],output,cancel.is_set,request.get('generation_settings',{}))
  except ExecutionStopUnconfirmed:
   self.execution_unknown=True;raise
  except Exception as error:
   self.execution_unknown=True
   raise ExecutionStopUnconfirmed('MUSIC_PROVIDER_RESULT_UNCONFIRMED')from error
  finally:self.running=False
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  if output.is_symlink()or not output.is_file()or output.resolve(strict=True).parent!=folder:raise ValueError('MUSIC_OUTPUT_SCOPE')
  sha=hashlib.sha256(output.read_bytes()).hexdigest()
  if not isinstance(provenance,dict)or provenance.get('audio_sha256')!=sha or provenance.get('synthetic_test_artifact')is not False:raise ValueError('MUSIC_OUTPUT_PROOF')
  with wave.open(str(output),'rb')as audio:
   rate=audio.getframerate();duration=audio.getnframes()/rate
   if rate!=48000 or audio.getnchannels()!=2 or audio.getsampwidth()!=2 or audio.getcomptype()!='NONE' or abs(duration-request['duration_seconds'])>.1:raise ValueError('MUSIC_WAV_FORMAT')
  receipt=folder/'music-result.json'
  # Only bounded provenance identifiers; no prompts, local paths or LM codes.
  safe={k:provenance[k]for k in ('model','model_version','runtime_commit','weights_snapshot','request_sha256','audio_sha256')if k in provenance and isinstance(provenance[k],str)}
  write_new(receipt,json.dumps({'requestId':claim.request_id,'attemptId':claim.attempt_id,'durationSeconds':duration,'sampleRate':rate,'provenance':safe},sort_keys=True).encode())
  stages=provenance.get('stages')
  if isinstance(stages,list)and stages and all(isinstance(row,dict)and type(row.get('pid'))is int and row['pid']>0 and type(row.get('exitCode'))is int and row['exitCode']==0 for row in stages):
   self._execution_proof={'kind':'local-processes','processGroups':[row['pid']for row in stages],'returnCodes':[row['exitCode']for row in stages],'outcome':'joined'}
  self._model_exit=deepcopy(self._execution_proof)
  return output,receipt


def hq_engine_factory(*,runtime_root,runner_path,python_path,lock_fds,lock_validator,enabled,timeout=1100):
 """Deployment-owned factory only. No runtime/model path comes from a request.
 Existing operator pins/grants remain required by HQBackend; enabled is explicit.
 """
 if enabled is not True or len(lock_fds)!=2 or not callable(lock_validator):raise ValueError('MUSIC_HQ_TRUSTED_BINDING_REQUIRED')
 def build(artifact_root):
  from workers.audio.hq.hq_backend import HQBackend
  return HQBackend(runtime_root=runtime_root,artifact_root=artifact_root,runner_path=runner_path,python_path=python_path,lock_fds=lock_fds,lock_validator=lock_validator,enabled=enabled,timeout=timeout)
 return build
