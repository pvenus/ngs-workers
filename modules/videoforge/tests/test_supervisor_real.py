import json,signal,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from videoforge_mcp import worker_loop

class RealSupervisorTests(unittest.TestCase):
 def fixture(self,root,name,status='queued'):
  job={'job_id':name*32,'status':status,'revision':1,'created_at':'2026-01-01T00:00:00Z'}
  path=root/'jobs'/(job['job_id']+'.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(job));return path
 def test_real_blocking_child_timeout_kill_and_next_fifo(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);jobs=root/'jobs';out=root/'out';out.mkdir()
   first=self.fixture(root,'a');second=self.fixture(root,'b');(out/('a'*32+'.partial.mp4')).write_bytes(b'partial')
   code="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
   with patch.object(worker_loop,'JOB_DIR',jobs),patch.object(worker_loop,'OUTPUT_DIR',out):
    self.assertTrue(worker_loop.supervise_once(.2,.05,[sys.executable,'-c',code]))
    self.assertEqual(json.loads(first.read_text())['error']['code'],'timeout')
    self.assertFalse((out/('a'*32+'.partial.mp4')).exists())
    self.assertEqual(json.loads(second.read_text())['status'],'queued')
    self.assertTrue(worker_loop.supervise_once(1,.05,[sys.executable,'-c','import sys;sys.exit(7)']))
    self.assertEqual(json.loads(second.read_text())['error']['code'],'worker_exit')
 def test_real_running_cancel_and_orphan_recovery(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);jobs=root/'jobs';out=root/'out';out.mkdir();first=self.fixture(root,'c')
   def cancel():
    time.sleep(.15);job=json.loads(first.read_text());job['status']='cancel_requested';first.write_text(json.dumps(job))
   thread=threading.Thread(target=cancel);thread.start()
   with patch.object(worker_loop,'JOB_DIR',jobs),patch.object(worker_loop,'OUTPUT_DIR',out):
    self.assertTrue(worker_loop.supervise_once(2,.05,[sys.executable,'-c','import time;time.sleep(30)']))
    thread.join();self.assertEqual(json.loads(first.read_text())['status'],'cancelled')
    orphan=self.fixture(root,'d','running');(out/('d'*32+'.partial.mp4')).write_bytes(b'p')
    self.assertEqual(worker_loop.recover_orphans(),1)
    self.assertEqual(json.loads(orphan.read_text())['error']['code'],'worker_interrupted')
    self.assertFalse((out/('d'*32+'.partial.mp4')).exists())
if __name__=='__main__':unittest.main()
