from __future__ import annotations
import time
from pathlib import Path
from .queue import JobQueue,now
from .contracts import sha256

def run_one(queue:JobQueue,profiles:dict,backends:dict,output_root:Path,clock=time.monotonic):
 job,lock=queue.claim_one()
 if job is None:return None
 try:
  profile=profiles[job['profile_id']];backend=backends[profile['backend']]
  limit=min(int(job['request'].get('timeout_seconds',1800)),int(profile['limits']['timeout_seconds']))
  start=clock();output_root.mkdir(parents=True,exist_ok=True);output=output_root/(job['job_id']+'.mp4')
  def cancelled():return queue.get(job['job_id'])['status']=='cancel_requested' or clock()-start>limit
  try:
   artifact=backend.generate(job['request'],profile,output,cancelled)
   if clock()-start>limit:raise TimeoutError('job_timeout')
   if cancelled():raise InterruptedError('cancelled')
   if not output.is_file():raise RuntimeError('output_missing')
   job.update(status='completed',artifact={'path':str(output),'sha256':sha256(output),**artifact})
  except TimeoutError as exc:job.update(status='failed',error={'code':'timeout','message':str(exc)});output.unlink(missing_ok=True)
  except InterruptedError:job.update(status='cancelled');output.unlink(missing_ok=True)
  except Exception as exc:job.update(status='failed',error={'code':type(exc).__name__,'message':str(exc)[:200]});output.unlink(missing_ok=True)
  job.update(completed_at=now(),revision=job['revision']+1);queue.save(job);return job
 finally:lock.close()
