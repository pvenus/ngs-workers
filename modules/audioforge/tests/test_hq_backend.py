"""Small synthetic subprocess/receipt fixtures. No weights or model imports."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from audioforge.backends import Cancelled
from audioforge.hq_backend import HQBackend
from audioforge.hq_contract import MODEL

FAKE_RUNNER = r'''
import sys,json,os,hashlib,wave
from pathlib import Path
stage,path=sys.argv[1:];r=json.loads(Path(path).read_text());p=Path(path).parent
base=dict(success=True,profileVersion=r['profile_version'],requestSha256=r['request_sha256'],
 runtimeRevision='ca1e85fe9430179831e6bc6be790c332190a3866',seed=r['seed'])
if stage=='plan':
 base.update(schemaVersion='ace-hq-plan.v1',model='acestep-5Hz-lm-4B',
 modelRevision='0a3ec94b557aea7d508da38b31cfe7341f6ff737',backend='mlx',dtype='bfloat16',
 duration=r['duration'],plannerPid=os.getpid(),audio_codes='<|audio_code_1|>',metadata={})
 (p/'lm-result.json').write_text(json.dumps(base))
else:
 plan=json.loads((p/'lm-result.json').read_text())
 try:os.kill(plan['plannerPid'],0)
 except ProcessLookupError:pass
 else:raise AssertionError('planner still alive')
 with wave.open(r['output'],'wb')as w:
  w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000)
  w.writeframes(b'\x01\x00\x01\x00'*int(48000*r['duration']))
 raw=Path(r['output']).read_bytes()
 base.update(schemaVersion='ace-hq-render.v1',model='acestep-v15-xl-sft',
 modelRevision='d06de46b4622f781cf07f4a013a67d591ca52819',backend='mps',dtype='float32',
 lmResultSha256=hashlib.sha256((p/'lm-result.json').read_bytes()).hexdigest(),
 audioSha256=hashlib.sha256(raw).hexdigest(),audioBytes=len(raw),sampleRate=48000,
 channels=2,durationSeconds=r['duration'])
 (p/'render-result.json').write_text(json.dumps(base))
'''


class HQBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / 'source/.venv/bin').mkdir(parents=True)
        (self.root / 'source/.venv/bin/python').symlink_to(sys.executable)
        (self.root / 'cache').mkdir()
        (self.root / 'cache/gateway-generation.lock').touch(mode=0o600)
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.runner = self.root / 'fake_runner.py'
        self.runner.write_text(FAKE_RUNNER)
        self.backend = HQBackend(runtime_root=self.root, artifact_root=self.artifacts,
                                 runner_path=self.runner, enabled=True)
        self.output = self.artifacts / 'request/1.wav'
        self.verifier = patch.object(HQBackend, 'verify_runtime', return_value={'fixtureOnly': True})
        self.verifier.start()
        self.monitor = patch.object(HQBackend, '_memory', return_value={'pressure': 1, 'swapGiB': 0})
        self.monitor.start()

    def tearDown(self):
        self.monitor.stop(); self.verifier.stop(); self.temp.cleanup()

    def generate(self, **kwargs):
        args = dict(prompt='Exact fixture prompt', duration=10, seed=7, model=MODEL,
                    output=self.output, cancelled=lambda: False, settings={'bpm': 80})
        args.update(kwargs)
        return self.backend.generate(**args)

    def test_two_real_synthetic_processes_exit_before_next_stage(self):
        result = self.generate()
        self.assertEqual([s['stage'] for s in result['stages']], ['plan', 'render'])
        self.assertNotEqual(result['stages'][0]['pid'], result['stages'][1]['pid'])
        self.assertEqual(result['audio_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        request = json.loads((self.output.parent / '1.hq/request.json').read_text())
        self.assertEqual(request['prompt'], 'Exact fixture prompt')
        self.assertEqual(request['settings'], {'bpm': 80})
        with self.assertRaises(ValueError): self.generate()  # No overwrite/replay.

    def test_disabled_and_invalid_never_spawn(self):
        with patch('audioforge.hq_backend.subprocess.Popen', side_effect=AssertionError('no spawn')):
            self.backend.activation = False
            with self.assertRaisesRegex(RuntimeError, 'NOT_ACTIVATED'): self.generate()
            self.backend.activation = True
            for change in ({'seed': True}, {'duration': 9}, {'settings': {'bpm': True}},
                           {'settings': {'instrumental': True, 'lyrics': 'voice'}},
                           {'model': 'acestep-v15-turbo'}):
                with self.assertRaises(ValueError): self.generate(**change)
        self.assertFalse(self.output.parent.exists())

    def test_plan_failure_never_starts_render_or_replays(self):
        with patch.object(self.backend, '_stage', side_effect=RuntimeError('plan failed')) as stage:
            with self.assertRaisesRegex(RuntimeError, 'plan failed'): self.generate()
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(stage.call_args.args[0], 'plan')
            with self.assertRaises(FileExistsError): self.generate()
        self.assertFalse(self.output.exists())
        self.assertTrue((self.output.parent / '1.hq/failed.json').exists())

    def test_cancel_no_intent(self):
        with self.assertRaises(Cancelled): self.generate(cancelled=lambda: True)
        self.assertFalse(self.output.parent.exists())

    def test_memory_critical_before_start(self):
        with patch.object(self.backend, '_memory', return_value={'pressure': 4, 'swapGiB': 0}), \
             patch.object(self.backend, '_stage', side_effect=AssertionError('no stage')):
            with self.assertRaisesRegex(RuntimeError, 'UNSAFE_BEFORE_START'): self.generate()

    def test_environment_credentials_and_watermark_not_inherited(self):
        with patch.dict(os.environ, {'HF_TOKEN': 'fake', 'PYTHONPATH': 'fake',
                                    'HTTPS_PROXY': 'fake', 'PYTORCH_MPS_HIGH_WATERMARK_RATIO': '0'}):
            env = self.backend._environment()
        for key in ('HF_TOKEN', 'PYTHONPATH', 'HTTPS_PROXY', 'PYTORCH_MPS_HIGH_WATERMARK_RATIO'):
            self.assertNotIn(key, env)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')


if __name__ == '__main__': unittest.main()
