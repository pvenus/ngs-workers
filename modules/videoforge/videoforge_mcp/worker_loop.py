from __future__ import annotations
import json,os,subprocess,sys,time
from pathlib import Path
from .config import JOB_DIR,OUTPUT_DIR
from .contracts import atomic_json
from .queue import now

def recover_orphans()->int:
 count=0
 for path in JOB_DIR.glob('*.json'):
  job=json.loads(path.read_text())
  if job.get('status') in ('running','cancel_requested'):
   job.update(status='failed',error={'code':'worker_interrupted','message':'supervisor restarted'},revision=job['revision']+1,completed_at=now())
   atomic_json(path,job)
   for suffix in ('.partial.mp4','.mp4'):(OUTPUT_DIR/(job['job_id']+suffix)).unlink(missing_ok=True)
   count+=1
 return count

def supervise_once(timeout_seconds:int=1800,poll_seconds:float=2,command:list[str]|None=None)->bool:
 JOB_DIR.mkdir(parents=True,exist_ok=True)
 queued=[]
 for path in JOB_DIR.glob('*.json'):
  job=json.loads(path.read_text())
  if job.get('status')=='queued':queued.append((job['created_at'],path))
 if not queued:return False
 _,target=min(queued)
 process=subprocess.Popen(command or [sys.executable,'-m','videoforge_mcp.run_once'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 started=time.monotonic()
 while process.poll() is None:
  elapsed=time.monotonic()-started
  job=json.loads(target.read_text())
  reason='timeout' if elapsed>timeout_seconds else ('cancelled' if job['status']=='cancel_requested' else None)
  if reason:
   process.terminate()
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.kill();process.wait()
   job=json.loads(target.read_text())
   job.update(status='failed' if reason=='timeout' else 'cancelled',revision=job['revision']+1,completed_at=now())
   if reason=='timeout':job['error']={'code':'timeout','message':'job_timeout'}
   atomic_json(target,job)
   for suffix in ('.partial.mp4','.mp4'):(OUTPUT_DIR/(job['job_id']+suffix)).unlink(missing_ok=True)
   break
  time.sleep(poll_seconds)
 job=json.loads(target.read_text())
 if job.get('status') in ('queued','running','cancel_requested'):
  code=getattr(process,'returncode',None)
  job.update(status='failed',error={'code':'worker_exit','message':f'worker exit {code}'},revision=job['revision']+1,completed_at=now())
  atomic_json(target,job)
  for suffix in ('.partial.mp4','.mp4'):(OUTPUT_DIR/(job['job_id']+suffix)).unlink(missing_ok=True)
 return True

def main():
 profile=json.loads(Path(os.environ['VIDEOFORGE_PROFILE_PATH']).read_text())
 timeout=int(profile['limits']['timeout_seconds'])
 recover_orphans()
 while True:
  if not supervise_once(timeout):time.sleep(2)
if __name__=='__main__':main()
