"""Explicit native output adapter; reuse original Wan and process lifetime.

Never selected by legacy role_factory. Original decode frames bypass MP4 encode,
while admission, memory policy, model lease and owned server shutdown are intact.
"""
import hashlib
from pathlib import Path
from .animation import AnimationBackend
from .animation_wan import run
from .animation_native_png_producer import produce_native_png_bundle,CAPABILITY,OUTPUT_MODE

class NativeAnimationBackend(AnimationBackend):
 def __init__(self,config,*,read_source,**options):
  if not callable(read_source):raise ValueError('NATIVE_PNG_SOURCE_READER_REQUIRED')
  if options.get('keep_model_idle',False):raise ValueError('NATIVE_PNG_RETAINED_NOT_CONFIGURED')
  self.read_source=read_source;self.active_claim=None;self.bundle=None
  super().__init__(config,engine=self._native_engine,**options)
 def load(self):
  import psutil # same dependency as the actual original engine
  super().load()
 def _native_engine(self,root,job,session,cancel):
  # This marker is included in original started.json's immutable jobHash.
  return run(root,{**job,'outputMode':OUTPUT_MODE},session,cancel,decoded_output=self._decoded)
 def _decoded(self,root,job,fit):
  claim=self.active_claim;task=claim.parameters
  binding={k:task[k]for k in ('requestId','npcId','generationId','posePreset','presetId','direction','sourceBindingHash')}
  frozen=task['frozenInputs']
  if (job['frameCount'],job['fps'])!=(frozen['motion']['frameCount'],frozen['motion']['fps']):raise ValueError('NATIVE_PNG_TIMING_CHANGED')
  self.bundle=produce_native_png_bundle(capability=CAPABILITY,output_mode=OUTPUT_MODE,
   binding=binding,expected_binding=binding,source_binding=frozen,
   read_current_source_binding=lambda:self.read_source(claim),decode_root=Path(root)/'frames',
   output_root=Path(root)/'native-output',frame_count=job['frameCount'],fps=job['fps'])
  path=self.bundle.manifest_path;data=path.read_bytes()
  return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),mimeType='application/json',
              frameCount=job['frameCount'],fps=job['fps'],outputMode=OUTPUT_MODE)
 def execute(self,claim,workspace,cancel):
  if claim.parameters.get('schemaVersion')!='ngs.animation-native-sequence-direction.v1':raise ValueError('NATIVE_PNG_CLAIM_REQUIRED')
  if self.active_claim is not None:raise ValueError('NATIVE_PNG_ALREADY_EXECUTING')
  self.active_claim=claim;self.bundle=None
  try:
   result=super().execute(claim,workspace,cancel)
   if self.bundle is None:
    # Existing completed native output needs formal receipt reconciliation;
    # do not silently rebuild or treat a cached MP4 as native PNG.
    raise ValueError('NATIVE_PNG_RECONCILIATION_REQUIRED')
   return (*result,*self.bundle.artifacts)
  finally:self.active_claim=None
