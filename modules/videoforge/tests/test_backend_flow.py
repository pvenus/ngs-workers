import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import imageio.v3 as iio
from videoforge_mcp.frames import extract_candidates
from videoforge_mcp.queue import JobQueue
from videoforge_mcp.runner import run_one
from videoforge_mcp.paths import confined

class FakeBackend:
 def __init__(self,mode='success'):self.mode=mode
 def generate(self,request,profile,output,cancelled):
  if self.mode=='cancel':raise InterruptedError()
  if self.mode=='fail':raise RuntimeError('mock failure')
  output.write_bytes(b'mp4');return {'duration_seconds':1}

class FlowTests(unittest.TestCase):
 def test_png_boundaries_end_to_end(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for n in (1,2,59,60,120):
    source=root/f'{n}.mp4';frames=[np.full((8,8,3),i%256,dtype=np.uint8) for i in range(n)]
    iio.imwrite(source,frames,fps=8,codec='libx264',macro_block_size=1)
    m=extract_candidates(source,root/f'out{n}',30,root,root)
    ids=[x['frame_index'] for x in m['candidates']]
    self.assertEqual(len(ids),min(n,60));self.assertEqual(ids[0],0);self.assertEqual(ids[-1],n-1);self.assertEqual(len(ids),len(set(ids)))
    self.assertTrue(all(Path(x['path']).is_file() for x in m['candidates']))
    self.assertEqual(m['source']['frame_count'],n)
 def test_path_escape(self):
  with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
   outside=Path(b)/'x';outside.write_bytes(b'x')
   with self.assertRaises(ValueError):confined(outside,Path(a),True)
 def test_worker_states(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'input.png').write_bytes(b'x');q=JobQueue(p/'jobs',p)
   req={'idempotency_key':'one','profile_id':'p','input_image':{'path':str(p/'input.png'),'consent':True,'rights_attested':True},'timeout_seconds':10}
   profile={'backend':'fake','limits':{'timeout_seconds':10}}
   with patch('videoforge_mcp.queue.validate_capacity'):
    for mode,status in (('success','completed'),('cancel','cancelled'),('fail','failed')):
     job=q.submit({**req,'idempotency_key':mode});done=run_one(q,{'p':profile},{'fake':FakeBackend(mode)},p/'out')
     self.assertEqual(done['status'],status);self.assertEqual(q.get(job['job_id'])['status'],status)
    job=q.submit({**req,'idempotency_key':'timeout','timeout_seconds':1})
    ticks=iter((0,2,2));done=run_one(q,{'p':profile},{'fake':FakeBackend()},p/'out',clock=lambda:next(ticks))
    self.assertEqual(done['status'],'failed');self.assertEqual(done['error']['code'],'timeout')
if __name__=='__main__':unittest.main()

class SupervisorTests(unittest.TestCase):
 def test_hard_timeout_marks_failed_and_removes_partial(self):
  import subprocess
  from videoforge_mcp import worker_loop
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);jobs=root/'jobs';out=root/'out';jobs.mkdir();out.mkdir();job={'job_id':'a'*32,'status':'running','revision':1,'created_at':'2026-01-01T00:00:00Z'}
   target=jobs/(job['job_id']+'.json');target.write_text(json.dumps(job))
   # A queued record exists at supervisor startup, then the mocked child changes it to running.
   job['status']='queued';target.write_text(json.dumps(job))
   class Proc:
    def poll(self):return None
    def terminate(self):pass
    def wait(self,timeout=None):return 0
   original=worker_loop.time.monotonic
   with patch.object(worker_loop,'JOB_DIR',jobs),patch.object(worker_loop,'OUTPUT_DIR',out),patch.object(worker_loop.subprocess,'Popen',return_value=Proc()),patch.object(worker_loop.time,'monotonic',side_effect=[0,2]):
    self.assertTrue(worker_loop.supervise_once(timeout_seconds=1,poll_seconds=0))
   self.assertEqual(json.loads(target.read_text())['error']['code'],'timeout')

class SupervisorRecoveryTests(unittest.TestCase):
 def test_timeout_then_next_fifo_job_is_available(self):
  from videoforge_mcp import worker_loop
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);jobs=root/'jobs';out=root/'out';jobs.mkdir();out.mkdir()
   first={'job_id':'a'*32,'status':'queued','revision':1,'created_at':'2026-01-01T00:00:00Z'}
   second={'job_id':'b'*32,'status':'queued','revision':1,'created_at':'2026-01-01T00:00:01Z'}
   for job in (first,second):(jobs/(job['job_id']+'.json')).write_text(json.dumps(job))
   (out/(first['job_id']+'.partial.mp4')).write_bytes(b'partial')
   class Blocking:
    def poll(self):return None
    def terminate(self):pass
    def wait(self,timeout=None):return 0
   class Finished:
    def poll(self):return 0
   with patch.object(worker_loop,'JOB_DIR',jobs),patch.object(worker_loop,'OUTPUT_DIR',out),patch.object(worker_loop.subprocess,'Popen',side_effect=[Blocking(),Finished()]) as spawn,patch.object(worker_loop.time,'monotonic',side_effect=[0,2,3]):
    self.assertTrue(worker_loop.supervise_once(timeout_seconds=1,poll_seconds=0))
    self.assertEqual(json.loads((jobs/(first['job_id']+'.json')).read_text())['error']['code'],'timeout')
    self.assertFalse((out/(first['job_id']+'.partial.mp4')).exists())
    self.assertEqual(json.loads((jobs/(second['job_id']+'.json')).read_text())['status'],'queued')
    self.assertTrue(worker_loop.supervise_once(timeout_seconds=1,poll_seconds=0))
    self.assertEqual(spawn.call_count,2)
