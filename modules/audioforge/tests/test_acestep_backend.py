"""Adapter tests use a tiny fake child process, never a model runtime."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from collections import namedtuple
from audioforge.acestep_backend import AceStepBackend
from audioforge.backends import Cancelled

FAKE = '''import json,sys,wave,time
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
if r['prompt']=='wait':time.sleep(30)
if r['prompt']=='fail':sys.exit(3)
with wave.open(r['output'],'wb') as w:
 w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000)
 w.writeframes(b'\\x01\\x00'*2*int(48000*r['duration']))
'''

class AceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'source').mkdir();self.art=self.root/'artifacts'
        self.adapter=AceStepBackend(self.root,self.art,timeout=2)
        self.adapter.python=Path(sys.executable)
        self.adapter.runner=self.root/'fake.py';self.adapter.runner.write_text(FAKE)
        self.output=self.art/'a'/'audio.wav'
        usage=namedtuple('usage','total used free')(100*1024**3,0,100*1024**3)
        self.disk=patch('audioforge.acestep_backend.shutil.disk_usage',return_value=usage);self.disk.start()
    def tearDown(self):self.disk.stop();self.temp.cleanup()
    def run_job(self,prompt='original prompt',cancelled=lambda:False):
        return self.adapter.generate(prompt,.25,21,'acestep-v15-turbo',self.output,cancelled,{'bpm':84})
    def test_success_unchanged_prompt_and_actual_rate(self):
        r=self.run_job();self.assertEqual(r['sample_rate'],48000)
        req=json.loads((self.output.parent/'ace-request.json').read_text())
        self.assertEqual(req['prompt'],'original prompt');self.assertEqual(req['settings'],{'bpm':84})
    def test_cancel_running_child(self):
        checks=iter([False,False,True])
        with self.assertRaises(Cancelled):self.run_job('wait',lambda:next(checks,True))
        self.assertFalse(self.output.exists())
    def test_timeout_and_failure(self):
        self.adapter.timeout=.05
        with self.assertRaises(TimeoutError):self.run_job('wait')
        with self.assertRaises(RuntimeError):self.run_job('fail')
        self.assertFalse(self.output.exists())
    def test_output_escape_and_settings(self):
        with self.assertRaises(ValueError):
            self.adapter.generate('p',.25,1,'acestep-v15-turbo',self.root/'escape.wav',lambda:False)
        with self.assertRaises(ValueError):
            self.adapter.generate('p',.25,1,'acestep-v15-turbo',self.output,lambda:False,{'inference_steps':16})
    def test_offline_and_default_allocator_environment(self):
        with patch.dict('os.environ',{'PYTORCH_MPS_HIGH_WATERMARK_RATIO':'0'}):
            env=self.adapter._environment()
        self.assertNotIn('PYTORCH_MPS_HIGH_WATERMARK_RATIO',env)
        self.assertEqual(env['HF_HUB_OFFLINE'],'1')

if __name__=='__main__':unittest.main()
