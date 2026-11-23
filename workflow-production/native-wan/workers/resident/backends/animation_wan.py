"""Owned offline Wan process. Fresh request conditioning, single-cycle reference, no legacy cached text."""
import copy,hashlib,json,pathlib,shutil,subprocess,sys,time,urllib.request,urllib.error

WAN_MODEL='wan_animate_2_distill_int8_convrot'
WAN_CHECKPOINT=WAN_MODEL+'.safetensors'

class ComfyHTTPError(urllib.error.HTTPError):
 """Keep existing HTTP/URL retry semantics, expose no URL or response content."""
 def __init__(self,label,status):
  super().__init__('',status,'',None,None)
  self.safe_code='ANIMATION_COMFY_'+label+'_HTTP_'+str(status)
 def __str__(self):return self.safe_code

def comfy_api(opener,url,path,data=None):
 req=urllib.request.Request(url+path,data=json.dumps(data).encode()if data is not None else None,headers={'Content-Type':'application/json'})
 try:
  with opener.open(req,timeout=15)as f:
   raw=f.read();return json.loads(raw)if raw.strip()else{}
 except urllib.error.HTTPError as exc:
  label={'/queue':'QUEUE','/prompt':'PROMPT','/free':'FREE'}.get(path)
  if label is None:label='HISTORY'if path.startswith('/history/')else'OTHER'
  if type(exc.code)is not int or not 100<=exc.code<=599:raise
  exc.close()  # Discard unread error body; never retain prompt or service details.
  raise ComfyHTTPError(label,exc.code)from None

def fit_reference(source,output,*,width=768,height=432,policy=None,reference_type='pixel',reference_layout='single'):
 """Downsample a native processed reference with motion-specific safety margins."""
 from PIL import Image
 policy=policy or {'schemaVersion':'ngs.animation-motion-safe-frame.v1','maxHeightRatio':0.72,'maxWidthRatio':0.60,'offsetY':0,'allowUpscale':False}
 if policy.get('schemaVersion')!='ngs.animation-motion-safe-frame.v1'or policy.get('allowUpscale')is not False or not 0.3<=policy.get('maxHeightRatio',0)<=0.75 or not 0.3<=policy.get('maxWidthRatio',0)<=0.75:raise ValueError('ANIMATION_FRAMING_POLICY_INVALID')
 image=Image.open(source).convert('RGBA');original=image.size
 if reference_type=='pixel'and min(original)<384:raise ValueError('ANIMATION_PIXEL_NATIVE_REPROCESS_REQUIRED')
 if reference_layout not in ('single','eight-up-4x2'):raise ValueError('ANIMATION_REFERENCE_LAYOUT_INVALID')
 if reference_layout=='eight-up-4x2':
  if (width,height)!=(768,432)or reference_type!='pixel':raise ValueError('ANIMATION_REFERENCE_LAYOUT_INVALID')
  scale=min(1.0,128/image.width,128/image.height);target=(max(1,round(image.width*scale)),max(1,round(image.height*scale)))
  scaled=image.resize(target,Image.Resampling.NEAREST);rgba=Image.new('RGBA',(width,height),(0,0,0,0));cells=[]
  for row in range(2):
   for col in range(4):
    origin=(col*192+(192-target[0])//2,row*216+(216-target[1])//2);rgba.alpha_composite(scaled,origin);cells.append({'origin':list(origin),'size':list(target)})
  rgba.save(output)
  return rgba,{'algorithm':'matched-eight-up-down-only-nearest.v1','referenceLayout':reference_layout,'sourceSize':list(original),'canvasSize':[width,height],'scaledSize':list(target),'copies':8,'cells':cells,'allReferenceCopiesSameView':True,'alphaBounds':list(rgba.getchannel('A').getbbox()or()),'alphaPreserved':True,'scale':scale,'outputFramingVerified':False,'finalFrameExtractionApplied':False}
 scale=min(1.0,width*policy['maxWidthRatio']/image.width,height*policy['maxHeightRatio']/image.height)
 target=(max(1,round(image.width*scale)),max(1,round(image.height*scale)))
 scaled=image.resize(target,Image.Resampling.NEAREST)
 rgba=Image.new('RGBA',(width,height),(0,0,0,0));rgba.alpha_composite(scaled,((width-target[0])//2,(height-target[1])//2))
 rgba.save(output)
 return rgba,{'algorithm':'motion-safe-down-only-nearest.v4','sourceSize':list(original),'canvasSize':[width,height],'framingPolicy':policy,'scaledSize':list(target),'alphaBounds':list(rgba.getchannel('A').getbbox()or()),'alphaPreserved':True,'scale':scale,'outputFramingVerified':False,'finalFrameExtractionApplied':False}

def _main_model_anchors(sample):
 # Comfy's classic cache keeps existing outputs whose input signatures occur
 # in the graph. Keep the same model+patched model keys in each phase without
 # adding any dependency that would execute a fresh model load outside sample.
 loader=sample.get('1');patch=sample.get('18')
 if (loader!={'class_type':'UNETLoader','inputs':{'unet_name':WAN_CHECKPOINT,'weight_dtype':'default'}}
     or patch!={'class_type':'ModelSamplingSD3','inputs':{'model':['1',0],'shift':5.0}}):
  raise ValueError('ANIMATION_RETAINED_MODEL_GRAPH_CHANGED')
 return {'1':loader,'18':patch}

def _with_model_anchors(graph,anchors):
 if not anchors:return graph
 if graph.keys()&anchors.keys():raise ValueError('ANIMATION_RETAINED_MODEL_NODE_COLLISION')
 return {**graph,**copy.deepcopy(anchors)}


def run(root,job,session,cancel,*,decoded_output=None):
 import psutil
 from .animation_conditioning import graph_for,read_with_retry
 binding=session.binding;C=binding['comfyRoot'];BASE=binding['base']
 retain_main_model=getattr(session,'retain_main_model',False)
 # Warm keeps model loader signatures identical and avoids invalidating the
 # actual model tensors via the global /free. All prompts and outputs remain
 # freshly namespaced per request; existing Comfy model management stays on.
 sample_template=json.loads(BASE.read_text())['prompt'] if retain_main_model else None
 anchors=_main_model_anchors(sample_template) if retain_main_model else None
 root=pathlib.Path(root);root.mkdir(parents=True,exist_ok=True);url=session.url;start=time.monotonic()
 def swap_used():
  try:return psutil.swap_memory().used
  except OSError:return None
 swap0=swap_used()
 def save(name,v):(root/name).write_text(json.dumps(v,ensure_ascii=False,indent=2))
 def api(path,data=None):
  return comfy_api(session.opener,url,path,data)
 if (root/'started.json').exists():
  old=json.loads((root/'started.json').read_text())
  if old.get('jobHash')!=hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest():raise ValueError('ANIMATION_NATIVE_JOB_CHANGED')
  if (root/'completed.json').exists():return json.loads((root/'completed.json').read_text())
  raise ValueError('ANIMATION_NATIVE_OUTCOME_UNKNOWN_NO_REEXECUTION')
 if (root/'completed.json').exists():raise ValueError('ANIMATION_COMPLETION_WITHOUT_BINDING')
 owned_live=retain_main_model and session.process is not None and session.process.poll() is None
 if not owned_live and psutil.virtual_memory().available<48*2**30:raise ValueError('ANIMATION_MEMORY_NOT_READY')
 session.ensure_ready(cancel)
 prefix='ngs-animation/'+job['requestId'];staged=session.input_root/prefix;staged.mkdir(parents=True,exist_ok=True)
 from PIL import Image
 ref,fit=fit_reference(job['referencePath'],root/'reference-normalized.png',policy=job.get('framingPolicy'),reference_type=job.get('referenceType','pixel'),reference_layout=job.get('referenceLayout','single'))
 canvas=Image.new('RGBA',(768,432),(32,32,32,255));canvas.alpha_composite(ref);canvas.convert('RGB').save(staged/'reference.png')
 fit.update(sourceSha256=hashlib.sha256(pathlib.Path(job['referencePath']).read_bytes()).hexdigest(),modelInputSha256=hashlib.sha256((staged/'reference.png').read_bytes()).hexdigest(),modelInputBackground='opaque charcoal composite for video conditioning')
 save('reference-fit.json',fit)
 shutil.copyfile(job['motionPath'],staged/'motion.mp4')
 if not(root/'started.json').exists():save('started.json',{'requestId':job['requestId'],'jobHash':hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest(),'startTime':time.time()})
 server=session.process
 def guard():
  if cancel.is_set():raise ValueError('ANIMATION_CANCELLED')
  if server.poll() is not None:raise ValueError('ANIMATION_COMFY_EXITED')
  available=psutil.virtual_memory().available;swap=swap_used();elapsed=time.monotonic()-start
  metric={'elapsedSeconds':round(elapsed,3),'availableGiB':available/2**30,'swapUsedGiB':swap/2**30 if swap is not None else None,'swapDeltaGiB':(swap-swap0)/2**30 if swap is not None and swap0 is not None else None}
  with (root/'memory.jsonl').open('a')as out:out.write(json.dumps(metric)+'\n')
  if elapsed>3000 or (swap is not None and swap0 is not None and swap-swap0>4*2**30) or available<8*2**30:raise ValueError('ANIMATION_RESOURCE_LIMIT')
 active_prompt=[None]
 def phase(name,graph):
  save(name+'.api.json',{'prompt':graph});q=api('/queue')
  if q['queue_running']or q['queue_pending']:raise ValueError('ANIMATION_COMFY_QUEUE_BUSY')
  response=api('/prompt',{'prompt':graph,'client_id':job['requestId']});save(name+'.submission.json',response);pid=response['prompt_id'];active_prompt[0]=pid;session.active_prompt=pid;t=time.monotonic()
  if getattr(session,'register_prompt',None):session.register_prompt(pid)
  while True:
   history=read_with_retry(lambda:api('/history/'+pid),guard,time.sleep)
   if pid in history:break
   time.sleep(2)
  save(name+'.history.json',history);value=history[pid]
  if value['status'].get('status_str')!='success' or not value['status'].get('completed'):raise ValueError('ANIMATION_NATIVE_PHASE_FAILED')
  save(name+'.timing.json',{'seconds':time.monotonic()-t});active_prompt[0]=None;session.active_prompt=None;session.last_phase={'promptId':pid,'phase':name,'history':value};return value
 try:
  for _ in range(120):
   guard()
   try:api('/queue');break
   except OSError:time.sleep(1)
  else:raise ValueError('ANIMATION_COMFY_START_TIMEOUT')
  g=graph_for(job)
  phase('conditioning',_with_model_anchors(g,anchors))
  if not retain_main_model:api('/free',{'unload_models':True,'free_memory':True})
  embeddings=session.root/'embeddings'/prefix/'conditionings';embeddings.mkdir(parents=True,exist_ok=True)
  for key in ['5','6','7']:
   files=list((session.output_root/prefix/'conditionings').glob(key+'_*.safetensors'))
   if len(files)!=1:raise ValueError('ANIMATION_CONDITIONING_OUTPUT_AMBIGUOUS')
   shutil.copyfile(files[0],embeddings/(key+'.safetensors'))
  from safetensors import safe_open
  shapes={}
  for key in ['5','6','7']:
   with safe_open(str(embeddings/(key+'.safetensors')),framework='pt',device='cpu')as f:shapes[key]={name:f.get_slice(name).get_shape()for name in f.keys()}
  save('conditioning-shapes.json',shapes)
  g=sample_template if retain_main_model else json.loads(BASE.read_text())['prompt']
  for key in ['5','6','7']:g[key]['inputs']['conditioning_name']=prefix+'/conditionings/'+key+'.safetensors'
  g['8']['inputs']['image']=prefix+'/reference.png';g['10']['inputs']['file']=prefix+'/motion.mp4';g['16']['inputs']['length']=job['frameCount']
  for key in ['9','12','16']:g[key]['inputs'].update(width=768,height=432)
  g['21']['inputs']['noise_seed']=job.get('seed',int(hashlib.sha256(job['requestId'].encode()).hexdigest()[:8],16))
  g['25']['inputs']['filename_prefix']=prefix+'/sample';result=phase('sample',g)
  loc=result['outputs']['25']['latents'][0];session.checked_output(loc)
  if not retain_main_model:api('/free',{'unload_models':True,'free_memory':True})
  g={'4':{'class_type':'VAELoader','inputs':{'vae_name':'Wan2_1_VAE_bf16.safetensors'}},'22':{'class_type':'LoadLatent','inputs':{'latent':str(pathlib.Path(loc['subfolder'])/loc['filename'])+' [output]'}},'23':{'class_type':'VAEDecodeTiled','inputs':{'samples':['22',0],'vae':['4',0],'tile_size':256,'overlap':32,'temporal_size':64,'temporal_overlap':4}},'24':{'class_type':'SaveImage','inputs':{'images':['23',0],'filename_prefix':prefix+'/decoded/frame'}}}
  result=phase('decode',_with_model_anchors(g,anchors));frames=result['outputs']['24']['images']
  if len(frames)!=job['frameCount']:raise ValueError('ANIMATION_FRAME_COUNT_MISMATCH')
  directory=root/'frames';directory.mkdir(exist_ok=True)
  for i,item in enumerate(frames):shutil.copyfile(session.checked_output(item),directory/f'{i:05d}.png')
  if decoded_output is not None:
   receipt=decoded_output(root,job,fit)
  else:
   output=root/'animation.mp4';session.encode(['/opt/homebrew/bin/ffmpeg','-y','-v','error','-framerate',str(job['fps']),'-i',str(directory/'%05d.png'),'-frames:v',str(job['frameCount']),'-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(output)],cancel)
   data=output.read_bytes();receipt={'path':str(output),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mimeType':'video/mp4','seconds':time.monotonic()-start,'model':WAN_MODEL,'modelCalls':1,'conditioningCalls':1,'frameCount':len(frames),'fps':job['fps'],'cycleCount':1,'visualCycleVerified':False,'referenceFit':fit}
  q=api('/queue')
  if q.get('queue_running')or q.get('queue_pending'):raise ValueError('ANIMATION_EXECUTION_NOT_IDLE')
  session.terminal={'engineRequestId':job['requestId'],**session.last_phase,'queue':q,'artifactSha256':receipt['sha256']}
  save('execution-idle.json',{'requestId':job['requestId'],'serverPid':server.pid,'queueRunning':[],'queuePending':[],'completedArtifactSha256':receipt['sha256']})
  save('completed.json',receipt);return receipt
 finally:
  # Successful phase outputs are durable before return. Cancellation/failure must
  # quiesce the owned server before the Request service can release its slot.
  if sys.exc_info()[0] is not None or active_prompt[0]:session.unload()
