from __future__ import annotations
from pathlib import Path
from typing import Protocol,Callable

class VideoBackend(Protocol):
 def generate(self,request:dict,profile:dict,output:Path,cancelled:Callable[[],bool])->dict:...

class LTXVideoBackend:
 def generate(self,request:dict,profile:dict,output:Path,cancelled:Callable[[],bool])->dict:
  if cancelled():raise InterruptedError('cancelled')
  model_dir=Path(request['model_dir']).resolve(strict=True)
  if not (model_dir/profile['model']['artifact']).is_file():raise FileNotFoundError('model_not_installed')
  import torch
  from diffusers import LTXImageToVideoPipeline
  from diffusers.utils import export_to_video,load_image
  pipe=LTXImageToVideoPipeline.from_pretrained(str(model_dir),torch_dtype=torch.bfloat16,local_files_only=True)
  pipe.to('mps' if torch.backends.mps.is_available() else 'cpu')
  image=load_image(request['input_image']['path'])
  try:
   result=pipe(image=image,prompt=request['prompt'],negative_prompt=request.get('negative_prompt',''),width=request['width'],height=request['height'],num_frames=request['frames'],num_inference_steps=request.get('steps',8),generator=torch.Generator(device='cpu').manual_seed(request['seed']))
   if cancelled():raise InterruptedError('cancelled')
   temp=output.with_suffix('.partial.mp4');export_to_video(result.frames[0],str(temp),fps=request['fps']);temp.replace(output)
   return {'duration_seconds':request['frames']/request['fps'],'width':request['width'],'height':request['height'],'fps':request['fps'],'frames':request['frames']}
  finally:
   del pipe
   if torch.backends.mps.is_available():torch.mps.empty_cache()
