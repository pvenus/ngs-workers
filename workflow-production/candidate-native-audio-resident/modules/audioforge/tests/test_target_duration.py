from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import wave

from audioforge import ace_hq_runner as runner
from audioforge import hq_backend as hq
from audioforge import hq_contract as contract


class TargetDuration(unittest.TestCase):
    def run_case(self, drafts, *, duration=None, target=True, fail_attempt=None, no_qualified=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend = object.__new__(hq.HQBackend)
            backend.root = backend.artifacts = root
            backend.activation = True
            backend.resident = {}
            backend.timeout = 1100
            backend._locks = lambda: nullcontext(())
            backend.verify_runtime = lambda *_a, **_k: {}
            backend._memory = lambda: {'pressure': 0, 'swapGiB':0}
            out = root / 'content.workflow-bgm-fixture' / 'audio.wav'
            calls = []
            def stage(name, path, *_):
                req = hq._read_json(path)
                plan_path = path.parent / 'lm-result.json'
                self.assertEqual(str(path.parent / 'audio.wav'), req['output'])
                sha = req['request_sha256']
                self.assertEqual(sha, hq.hashlib.sha256(contract.canonical({
                    k:v for k,v in req.items() if k!='request_sha256'}).encode()).hexdigest())
                calls.append((name, path.parent.name, req['seed'], sha))
                if name == 'plan':
                    index = len(calls)-1
                    if fail_attempt == index + 1:
                        raise RuntimeError('HQ_STAGE_DIAGNOSTIC:plan:HQ_LM_GENERATION_FAILED')
                    planned = drafts[index]
                    hq._write_new(plan_path, dict(schemaVersion='ace-hq-plan.v1', success=True,
                      profileVersion=contract.PROFILE_VERSION, requestSha256=sha,
                      model='acestep-5Hz-lm-4B', modelRevision=contract.LM_REVISION,
                      runtimeRevision=contract.RUNTIME_REVISION, backend='mlx', dtype='bfloat16',
                      seed=req['seed'], plannerPid=100, duration=planned,
                      metadata=dict(duration=planned)))
                else:
                    bound = hq._read_json(plan_path)
                    planned = bound['duration']
                    self.assertEqual(bound['requestSha256'], sha)
                    audio = Path(req['output'])
                    with wave.open(str(audio), 'wb') as wav:
                        wav.setparams((2,2,48000,0,'NONE','not compressed'))
                        for _ in range(int(planned)):
                            wav.writeframesraw(bytes(48000*4))
                    hq._write_new(path.parent / 'render-result.json', dict(schemaVersion='ace-hq-render.v1',
                      success=True, profileVersion=contract.PROFILE_VERSION, requestSha256=sha,
                      model='acestep-v15-xl-sft', modelRevision=contract.XL_REVISION,
                      runtimeRevision=contract.RUNTIME_REVISION, backend='mps', dtype='float32',
                      lmResultSha256=hq._sha(plan_path), seed=req['seed'], sampleRate=48000,
                      channels=2, durationSeconds=planned, audioSha256=hq._sha(audio),
                      audioBytes=audio.stat().st_size))
                return dict(pid=100, stage=name)
            backend._stage = stage
            settings = {'target_duration_seconds':60} if target else {}
            with patch.object(hq.shutil, 'disk_usage', return_value=SimpleNamespace(free=20*1024**3)):
                if fail_attempt:
                    with self.assertRaisesRegex(RuntimeError, 'HQ_LM_GENERATION_FAILED'):
                        backend.generate('caption',duration,42,contract.MODEL,out,lambda:False,settings)
                    self.assertFalse(out.exists())
                    return None, calls
                if no_qualified:
                    with self.assertRaisesRegex(ValueError, 'HQ_LM_FULL_DURATION_NOT_MET'):
                        backend.generate('caption', duration, 42, contract.MODEL, out, lambda:False, settings)
                    self.assertFalse(out.exists())
                    return None, calls
                proof = backend.generate('caption',duration,42,contract.MODEL,out,lambda:False,settings)
            self.assertEqual(proof, json.loads((out.parent / 'audio.hq/completion.json').read_text()))
            self.assertTrue(out.is_file())
            return proof, calls

    def test_reaches_target_and_renders_once_with_selected_pair(self):
        proof, calls = self.run_case([32, 60, 90])
        self.assertEqual([(kind, root) for kind,root,_,_ in calls], [
            ('plan','attempt-1'),('plan','attempt-2'),('render','attempt-2')])
        self.assertEqual(proof['plan_attempt'], 2)
        self.assertEqual(proof['seed'], contract.target_plan_seed(42,2))
        self.assertEqual(calls[-1][2:], calls[1][2:])
        self.assertEqual(proof['duration_seconds'], 60)

    def test_short_plans_exhaust_attempts_without_render_or_publication(self):
        proof, calls = self.run_case([36, 55, 51, 55, 48], no_qualified=True)
        self.assertEqual([x[0] for x in calls], ['plan']*5)
        self.assertEqual(len({x[2] for x in calls}),5)
        self.assertIsNone(proof)

    def test_scoped_duration_vocabulary_preserves_exact_lyrics_and_restores_resident_on_error(self):
        for cap in (95, 360):
            with self.subTest(cap=cap):
                request = dict(duration=None, seed=44, prompt='Caption',
                    settings=dict(instrumental=False, lyrics='  첫 줄\n둘째 줄  \n', target_duration_seconds=60))
                spec = {'min':10,'max':cap}
                values = [str(i) for i in range(10,cap+1)]
                old_tree = {'prior':1}
                processor = SimpleNamespace(max_duration=cap, field_specs={'duration':spec},
                    valid_duration_values=values, duration_prefix_tree=old_tree,
                    _build_numeric_prefix_tree=lambda v, **_: {value:True for value in v})
                lm = SimpleNamespace(constrained_processor=processor)
                with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                    with runner.scoped_lm_duration_vocabulary(lm, request):
                        self.assertEqual(spec, {'min':60,'max':min(cap,120)})
                        self.assertEqual(processor.max_duration,min(cap,120))
                        self.assertEqual(processor.valid_duration_values, [str(i) for i in range(60,min(cap,120)+1)])
                        self.assertNotIn('59',processor.duration_prefix_tree)
                        self.assertIn('60',processor.duration_prefix_tree)
                        options=runner.lm_options(request)
                        self.assertTrue(options['use_constrained_decoding'])
                        self.assertTrue(options['use_cot_metas'])
                        self.assertEqual(options['lyrics'],request['settings']['lyrics'])
                        self.assertNotIn('duration',options['user_metadata'])
                        self.assertIsNone(options['target_duration'])
                        raise RuntimeError('fixture failure')
                self.assertEqual(spec, {'min':10,'max':cap})
                self.assertEqual(processor.max_duration,cap)
                self.assertIs(processor.valid_duration_values,values)
                self.assertIs(processor.duration_prefix_tree,old_tree)
                normal = {**request,'settings':{'instrumental':True}}
                with runner.scoped_lm_duration_vocabulary(lm,normal):
                    self.assertEqual(spec,{'min':10,'max':min(cap,120)})
                    self.assertIn('21',processor.duration_prefix_tree)
                self.assertIs(processor.duration_prefix_tree,old_tree)

    def test_no_full_length_when_existing_hardware_cap_below_target(self):
        processor = SimpleNamespace(max_duration=55, field_specs={'duration':{'min':10,'max':55}},
            valid_duration_values=[], duration_prefix_tree={}, _build_numeric_prefix_tree=lambda *_a, **_k: self.fail())
        lm=SimpleNamespace(constrained_processor=processor)
        with self.assertRaisesRegex(RuntimeError, 'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE'):
            with runner.scoped_lm_duration_vocabulary(lm,dict(duration=None,settings={'target_duration_seconds':60})):
                self.fail('must not sample')

    def test_operational_failure_does_not_publish_earlier_short_plan(self):
        _, calls = self.run_case([50],fail_attempt=2)
        self.assertEqual([v[0] for v in calls],['plan','plan'])

    def test_legacy_auto_and_fixed_single_plan(self):
        for duration in (None, 20):
            proof, calls = self.run_case([20],duration=duration,target=False)
            self.assertNotIn('plan_attempt',proof)
            self.assertNotIn('seed',proof)
            self.assertEqual([(v[0],v[1],v[2]) for v in calls], [
                ('plan','audio.hq',42),('render','audio.hq',42)])

    def test_only_exact_opt_in_to_auto(self):
        for value, duration in [(60,30),(59,None),(True,None),(60.,None)]:
            with self.assertRaisesRegex(ValueError,'HQ_TARGET_DURATION'):
                contract.validate_call('caption',duration,1,contract.MODEL,{'target_duration_seconds':value})


if __name__ == '__main__':
    unittest.main()
