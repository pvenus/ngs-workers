"""Owned separation-cover contract tests; no model, network or production data."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import patch

from audioforge import ace_hq_runner as runner
from audioforge import hq_backend as hq
from audioforge import hq_contract as contract


class SourceCover(unittest.TestCase):
    def test_model_requests_cover_from_accompaniment_only_and_never_from_text_or_raw_mix(self):
        options=runner._cover_params({'duration':61.25,'prompt':'ignored','settings':{'lyrics':'ignored'}},Path('/fixture/accompaniment.wav'))
        self.assertEqual(options,{'task_type':'cover','src_audio':'/fixture/accompaniment.wav','reference_audio':None,
            'audio_cover_strength':.8,'cover_noise_strength':0.,'caption':runner.COVER_CAPTION,
            'lyrics':'[Instrumental]','instrumental':True,'duration':61.25,'vocal_language':'unknown'})
        self.assertNotIn('audio_codes',options)
        self.assertNotIn('ignored',str(options))

    def test_separator_fails_closed_without_owner_pin_and_rejects_link(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve()
            with self.assertRaisesRegex(RuntimeError,'HQ_SEPARATOR_UNPROVISIONED'):
                runner._separator_checkpoint(root)
            directory=root/'cache/separation';directory.mkdir(mode=0o700,parents=True)
            weight=directory/runner.SEPARATOR_WEIGHTS
            weight.write_bytes(b'placeholder only');weight.chmod(0o600)
            digest=hashlib.sha256(weight.read_bytes()).hexdigest()
            pin=directory/'pin.json';pin.write_text(json.dumps({'schemaVersion':'ace-hq-hdemucs-pin.v1','checkpointSha256':digest}))
            pin.chmod(0o600)
            self.assertEqual(runner._separator_checkpoint(root),(weight,digest))
            weight.unlink();weight.symlink_to(pin)
            with self.assertRaisesRegex(RuntimeError,'HQ_SEPARATOR_UNPROVISIONED'):
                runner._separator_checkpoint(root)

    def test_backend_skips_lm_and_checks_bound_cover_receipt_length_and_output_hash(self):
        for invalid in (None,'short','same','wrong lineage'):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as name:
                root=Path(name).resolve()
                backend=object.__new__(hq.HQBackend)
                backend.root=backend.artifacts=root
                backend.activation=True;backend.resident={}
                backend.timeout=1100;backend._locks=lambda:nullcontext(())
                backend.verify_runtime=lambda *_a,**_k:{}
                backend._memory=lambda:{'pressure':0,'swapGiB':0}
                scratch=root/'content.workflow-bgm-fixture';scratch.mkdir(mode=0o700)
                def pcm(path,length,mark):
                    with wave.open(str(path),'wb') as w:
                        w.setparams((2,2,48000,0,'NONE',''))
                        w.writeframes(bytes((mark,0,mark,0))*round(length*48000))
                source=scratch/'source.wav';source_out=scratch/'audio.wav'
                pcm(source,31.5,1);source.chmod(0o600)
                digest=hq._sha(source);calls=[]
                def stage(kind,request,*_):
                    calls.append(kind);value=hq._read_json(request)
                    self.assertEqual(value['cover_source_sha256'],digest)
                    self.assertEqual(value['settings'],{'instrumental':True})
                    self.assertIsNone(value['prompt'])
                    self.assertEqual(value['duration'],31.5)
                    a=Path(value['output']); accompaniment=request.parent/'accompaniment.wav'
                    pcm(accompaniment,31.5,2)
                    length=31.8 if invalid=='short' else 31.5
                    pcm(a,length,1 if invalid=='same' else 3)
                    hq._write_new(request.parent/'render-result.json',dict(schemaVersion='ace-hq-cover-result.v1',
                        success=True,profileVersion=contract.PROFILE_VERSION,requestSha256=value['request_sha256'],
                        model='acestep-v15-xl-sft',modelRevision=contract.XL_REVISION,runtimeRevision=contract.RUNTIME_REVISION,
                        backend='mps',dtype='float32',coverSourceSha256=digest,
                        accompanimentSha256='3'*64 if invalid=='wrong lineage' else hq._sha(accompaniment),
                        seed=1,sampleRate=48000,channels=2,audioSha256=hq._sha(a),audioBytes=a.stat().st_size,durationSeconds=length))
                backend._stage=stage
                with patch.object(hq.shutil,'disk_usage',return_value=SimpleNamespace(free=20*1024**3)):
                    if invalid:
                        with self.assertRaises(ValueError):
                            backend.generate_cover(31.5,1,source_out,lambda:False,digest)
                        self.assertFalse(source_out.exists())
                    else:
                        result=backend.generate_cover(31.5,1,source_out,lambda:False,digest)
                        self.assertEqual(result['duration_seconds'],31.5)
                        self.assertEqual(result['cover_source_sha256'],digest)
                        self.assertEqual(result['audio_sha256'],hq._sha(source_out))
                self.assertEqual(hq._sha(source),digest)
                self.assertEqual(calls,['render'])


if __name__=='__main__':
    unittest.main()
