import base64
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import wave

import hq_bgm as hq

COMMAND={'kind':'bgm','title':'테스트','prompt':'Daegeum and piano answer one another.', 'durationSeconds':10}

def renderer(t,folder,bpm=None,drift=False,auto=False,text="",duration_drift=False,target_attempt=None,inconsistent_seed=False,target_seconds=72):
    invoked=[]
    def generate(config,root,prompt,duration,seed,model,wav,stop,**options):
        invoked.append(root)
        t.assertEqual(model,hq.HQ_PROFILE)
        t.assertEqual(prompt,COMMAND['prompt'])
        t.assertEqual(duration,None if auto else 10)
        settings={'instrumental':False,'lyrics':text,'vocal_language':'ko'} if text else {}
        if target_attempt is not None:
            settings['target_duration_seconds']=60
            seed=(seed+(target_attempt-1)*0x9e3779b9)&0x7fffffff
        t.assertEqual(options['settings'],settings)
        actual=target_seconds if target_attempt is not None else 73 if auto else 10
        with wave.open(str(wav),'wb') as out:
            out.setparams((2,2,48000,0,'NONE','not compressed'))
            out.writeframes(b'\x01\x00\x01\x00'*(48000*actual))
        data=wav.read_bytes();wave_sha=sha256(data).hexdigest()
        job='f'*64
        working=root/'audio.hq'
        if target_attempt is not None:working/=f'attempt-{target_attempt}'
        working.mkdir(parents=True)
        plan={'schemaVersion':'ace-hq-plan.v1','success':True,'requestSha256':job,'model':'acestep-5Hz-lm-4B',
              'modelRevision':hq.LM_REV,'seed':seed,'duration':actual,
              'metadata':{**({'duration':actual+1 if duration_drift else actual} if auto else {}),
                          **({'bpm':bpm} if bpm is not None else {})}}
        raw=json.dumps(plan).encode();(working/'lm-result.json').write_bytes(raw)
        plan_sha=sha256(raw).hexdigest()
        render={'schemaVersion':'ace-hq-render.v1','success':True,'requestSha256':job,'audioSha256':wave_sha,
                'audioBytes':len(data),'lmResultSha256':plan_sha,'durationSeconds':actual}
        (working/'render-result.json').write_text(json.dumps(render))
        (working/'generation-settings.json').write_text(json.dumps({'requestSha256':job,'lmResultSha256':plan_sha,
            'params':{'bpm':900 if drift else bpm,'duration':actual,'instrumental':not text,'lyrics':text}}))
        return {'model':model,'request_sha256':job,'synthetic_test_artifact':False,'audio_sha256':wave_sha,
                'duration_seconds':actual,'planner_snapshot':hq.LM_REV,'weights_snapshot':hq.XL_REV,'runtime_commit':hq.RUNTIME_REV,
                **({'plan_attempt':target_attempt,'seed':seed+inconsistent_seed} if target_attempt is not None else {})}
    def connect(profile,binding):
        t.assertEqual(profile,hq.HQ_PROFILE)
        return SimpleNamespace(generate_music=generate),{'schema':'private.music-hq-boundary.v1','artifact_root':folder}
    return connect,invoked

class HQTests(unittest.TestCase):
    def test_one_wav_and_exact_model_bpm_or_unavailable(self):
        for bpm in (82,None):
            with TemporaryDirectory() as p:
                c,invoked=renderer(self,Path(p).resolve(),bpm)
                result=hq.render_hq(COMMAND,'unused fixture',connect=c)
                self.assertEqual((result['bpm'],result['bpmBasis']),(bpm,'model' if bpm is not None else 'unavailable'))
                self.assertEqual(base64.b64decode(result['wavBase64'])[:4],b'RIFF')
                self.assertEqual(len(invoked),1)
                self.assertFalse(invoked[0].exists())
    def test_auto_humming_preserves_exact_words_and_model_duration(self):
        import io
        with TemporaryDirectory() as p:
            text='[Instrumental]\n\n[Humming]\n음— 음—\n\n[Instrumental Outro]'
            cmd={**COMMAND,'durationSeconds':None,'text':text}
            c,invoked=renderer(self,Path(p).resolve(),80,auto=True,text=text)
            result=hq.render_hq(cmd,'fixture',connect=c)
            with wave.open(io.BytesIO(base64.b64decode(result['wavBase64'])),'rb') as wav:
                self.assertEqual(wav.getnframes()/wav.getframerate(),73)
            self.assertEqual(result['bpm'],80)
            self.assertEqual(len(invoked),1)
            self.assertFalse(invoked[0].exists())
            c,_=renderer(self,Path(p).resolve(),80,auto=True,text=text,duration_drift=True)
            with self.assertRaisesRegex(ValueError,'AUTO_DURATION_PROOF_MISMATCH'):
                hq.render_hq(cmd,'fixture',connect=c)
            with self.assertRaisesRegex(ValueError,'INPUT_INVALID'):
                hq.render_hq({**cmd,'durationSeconds':30},'fixture',connect=c)

    def test_full_target_accepts_bound_long_attempt_and_refuses_short_wav(self):
        import io
        with TemporaryDirectory() as p:
            text='  아아, 떠나소서.\n그대 가시는 길.  '
            cmd={**COMMAND,'durationSeconds':None,'targetDurationSeconds':60,'text':text}
            c,_=renderer(self,Path(p).resolve(),80,auto=True,text=text,target_attempt=3)
            output=hq.render_hq(cmd,'fixture',connect=c)
            with wave.open(io.BytesIO(base64.b64decode(output['wavBase64'])),'rb') as wav:
                self.assertEqual(wav.getnframes()/wav.getframerate(),72)
            c,_=renderer(self,Path(p).resolve(),80,auto=True,text=text,target_attempt=3,target_seconds=52)
            with self.assertRaisesRegex(ValueError,'FULL_DURATION_NOT_MET'):
                hq.render_hq(cmd,'fixture',connect=c)
            for attempt,wrong_seed in [(3,True),(6,False)]:
                c,_=renderer(self,Path(p).resolve(),80,auto=True,text=text,target_attempt=attempt,inconsistent_seed=wrong_seed)
                with self.assertRaisesRegex(ValueError,'AUTO_DURATION_PROOF_MISMATCH'):
                    hq.render_hq(cmd,'fixture',connect=c)
            with self.assertRaisesRegex(ValueError,'INPUT_INVALID'):
                hq.render_hq({**cmd,'durationSeconds':30},'fixture',connect=c)

    def test_mismatched_proof_and_no_turbo_fallback(self):
        with TemporaryDirectory() as p:
            c,invoked=renderer(self,Path(p).resolve(),80,True)
            with self.assertRaisesRegex(ValueError,'TEMPO_PROOF_MISMATCH'):
                hq.render_hq(COMMAND,'unused fixture',connect=c)
            self.assertEqual(len(invoked),1)
            self.assertFalse(invoked[0].exists())
            for bad in [{**COMMAND,'durationSeconds':9},{**COMMAND,'kind':'effect'},{**COMMAND,'bpm':80}]:
                with self.assertRaisesRegex(ValueError,'INPUT_INVALID'):
                    hq.render_hq(bad,'unused fixture',connect=c)
            self.assertEqual(len(invoked),1)

if __name__=='__main__':unittest.main()
