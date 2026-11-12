from __future__ import annotations
import fcntl,json,uuid
from datetime import datetime,timezone
from pathlib import Path
from .contracts import atomic_json,validate_capacity
from .paths import confined

def now():return datetime.now(timezone.utc).isoformat()
class JobQueue:
 def __init__(self,root:Path,input_root:Path|None=None):self.root=root;self.input_root=input_root
 def submit(self,request:dict,swap_bytes:int=0)->dict:
  image=request.get('input_image',{})
  if not image.get('consent') or not image.get('rights_attested'):raise ValueError('input_rights_required')
  if not request.get('idempotency_key'):raise ValueError('idempotency_key_required')
  if not request.get('profile_id'):raise ValueError('profile_id_required')
  if self.input_root is None:raise ValueError('input_root_required')
  image['path']=str(confined(Path(image['path']),self.input_root,True))
  self.root.mkdir(parents=True,exist_ok=True);validate_capacity(self.root,swap_bytes,int(request.get('timeout_seconds',1800)))
  for p in self.root.glob('*.json'):
   d=json.loads(p.read_text())
   if d.get('idempotency_key')==request['idempotency_key']:return d
  job={'job_id':uuid.uuid4().hex,'status':'queued','idempotency_key':request['idempotency_key'],'profile_id':request['profile_id'],'request':request,'created_at':now(),'revision':1}
  atomic_json(self.root/(job['job_id']+'.json'),job);return job
 def get(self,job_id:str)->dict:return json.loads(self._path(job_id).read_text())
 def _path(self,job_id:str)->Path:
  uuid.UUID(job_id);return self.root/(job_id+'.json')
 def save(self,job:dict)->None:atomic_json(self._path(job['job_id']),job)
 def cancel(self,job_id:str)->dict:
  job=self.get(job_id)
  if job['status']=='queued':job['status']='cancelled'
  elif job['status']=='running':job['status']='cancel_requested'
  job['revision']+=1;self.save(job);return job
 def claim_one(self):
  self.root.mkdir(parents=True,exist_ok=True);lock=(self.root/'.worker.lock').open('a+')
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:lock.close();return None,None
  queued=[]
  for p in self.root.glob('*.json'):
   d=json.loads(p.read_text())
   if d.get('status')=='queued':queued.append((d['created_at'],p,d))
  if not queued:lock.close();return None,None
  _,path,job=min(queued);job.update(status='running',started_at=now(),revision=job['revision']+1);atomic_json(path,job);return job,lock
